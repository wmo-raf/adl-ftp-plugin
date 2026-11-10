"""
A logger file that grows in place must not be re-ingested whole every run.

Campbell loggers append to one TOA5 table file. With re-downloading on, every
run fetched the grown file, decoded all of it, and handed core the entire
history — a year of hourly rows upserted every ten minutes to add one row,
until the run no longer fit its batch's time budget. Each file now carries a
high-water mark: the newest observation decoded from it. Once a file has been
processed, only rows newer than ``min(mark, start_date)`` go out — so a routine
run sends the new rows only, while gap recovery (an older ``start_date``) still
re-reads the hole.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from adl.core.models import ObservationRecord
from adl.core.registries import FLUSH
from adl.core.tests.factories import CelsiusUnitFactory, DataParameterFactory
from celery.exceptions import SoftTimeLimitExceeded
from django.test import override_settings
from django.utils import timezone as dj_timezone

from adl_ftp_plugin.models import FTPStationDataFile
from adl_ftp_plugin.tests.test_process_file import (
    MEDIA_TMP,
    FakeClient,
    FakeDecoder,
    ProcessFileTestCase,
)

HARARE = ZoneInfo("Africa/Harare")
FILE = "CR1000_Hourly.dat"


def hourly_rows(first, count):
    """Naive, station-local rows the way the TOA5 decoder produces them."""
    return [
        {"observation_time": first + timedelta(hours=i), "air_temperature_2m": 20.0 + i}
        for i in range(count)
    ]


def records_only(items):
    return [item for item in items if item is not FLUSH]


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class HighWaterMarkTestCase(ProcessFileTestCase):
    def setUp(self):
        super().setUp()
        self.connection.stations_timezone = HARARE
        self.connection.save()
        self.link.skip_already_downloaded_files = False
        self.link.save()
        self.first = datetime(2026, 11, 1, 0, 0)
        self.rows = hourly_rows(self.first, 5)

    def local(self, naive):
        return dj_timezone.make_aware(naive, HARARE)

    def run_file(self, rows, start_date=None):
        return list(self.plugin._process_file(
            self.link, "/data", FILE, FakeDecoder(rows), FakeClient(), start_date=start_date,
        ))

    def mark(self):
        return FTPStationDataFile.objects.get(station_link=self.link, file_name=FILE).latest_observation_time


class FirstProcessingTests(HighWaterMarkTestCase):
    def test_a_new_file_is_read_whole_and_stamped_with_its_newest_row(self):
        start = self.local(self.first + timedelta(hours=3))  # would cut rows, if applied
        items = self.run_file(self.rows, start_date=start)

        self.assertEqual(records_only(items), self.rows)
        self.assertEqual(self.mark(), self.local(self.first + timedelta(hours=4)))

    def test_naive_rows_are_read_in_the_station_timezone(self):
        self.run_file(self.rows)
        # 04:00 in Harare is 02:00 UTC
        self.assertEqual(self.mark(), datetime(2026, 11, 1, 2, 0, tzinfo=dt_timezone.utc))

    def test_a_downloaded_but_never_processed_file_is_read_whole(self):
        FTPStationDataFile.objects.create(station_link=self.link, file_name=FILE)
        items = self.run_file(self.rows, start_date=self.local(self.first + timedelta(hours=3)))
        self.assertEqual(records_only(items), self.rows)


class GrownFileTests(HighWaterMarkTestCase):
    def setUp(self):
        super().setUp()
        self.run_file(self.rows)  # first run: rows 00:00..04:00, mark 04:00
        self.grown = hourly_rows(self.first, 7)  # the logger appended 05:00 and 06:00

    def test_a_routine_run_sends_only_the_rows_appended_since(self):
        # Routine run: start_date is the latest saved observation == the mark
        items = self.run_file(self.grown, start_date=self.local(self.first + timedelta(hours=4)))

        self.assertEqual(records_only(items), self.grown[5:])
        self.assertEqual(self.mark(), self.local(self.first + timedelta(hours=6)))

    def test_gap_recovery_with_an_older_start_date_re_reads_the_hole(self):
        items = self.run_file(self.grown, start_date=self.local(self.first + timedelta(hours=1)))
        self.assertEqual(records_only(items), self.grown[2:])

    def test_a_mark_ahead_of_the_saved_data_yields_to_the_start_date(self):
        # Rows up to 04:00 were decoded, but the latest *saved* is 02:00
        items = self.run_file(self.grown, start_date=self.local(self.first + timedelta(hours=2)))
        self.assertEqual(records_only(items), self.grown[3:])

    def test_a_start_date_ahead_of_the_mark_does_not_skip_this_files_new_rows(self):
        # Another file of the station's already reached 05:30
        items = self.run_file(self.grown, start_date=self.local(self.first + timedelta(hours=5, minutes=30)))
        self.assertEqual(records_only(items), self.grown[5:])

    def test_an_unchanged_file_sends_nothing_but_keeps_its_mark(self):
        items = self.run_file(self.rows, start_date=self.local(self.first + timedelta(hours=4)))
        self.assertEqual(items, [FLUSH])
        self.assertEqual(self.mark(), self.local(self.first + timedelta(hours=4)))

    def test_the_mark_does_not_advance_when_the_run_is_cut_short(self):
        gen = self.plugin._process_file(
            self.link, "/data", FILE, FakeDecoder(self.grown), FakeClient(),
            start_date=self.local(self.first + timedelta(hours=4)),
        )
        next(gen)  # 05:00 handed to core
        with self.assertRaises(SoftTimeLimitExceeded):
            gen.throw(SoftTimeLimitExceeded())
        self.assertEqual(self.mark(), self.local(self.first + timedelta(hours=4)))


class LegacyFileTests(HighWaterMarkTestCase):
    def test_a_file_processed_before_marks_existed_is_bounded_by_the_start_date(self):
        FTPStationDataFile.objects.create(
            station_link=self.link, file_name=FILE, processed_at=dj_timezone.now(),
        )
        items = self.run_file(self.rows, start_date=self.local(self.first + timedelta(hours=3)))

        self.assertEqual(records_only(items), self.rows[4:])
        self.assertEqual(self.mark(), self.local(self.first + timedelta(hours=4)))


class SoftTimeLimitDuringDecodeTests(HighWaterMarkTestCase):
    def test_the_soft_limit_while_decoding_is_not_swallowed_as_a_decode_error(self):
        with self.assertRaises(SoftTimeLimitExceeded):
            list(self.plugin._process_file(
                self.link, "/data", FILE, FakeDecoder(error=SoftTimeLimitExceeded()), FakeClient(),
            ))


class ThroughCoreTests(HighWaterMarkTestCase):
    """Two runs through core's real save loop: the second upserts only the new row."""

    def setUp(self):
        super().setUp()
        unit_c = CelsiusUnitFactory()
        param = DataParameterFactory(name="air_temperature", unit=unit_c)
        mapping = SimpleNamespace(
            id=1, adl_parameter=param,
            source_parameter_name="air_temperature_2m", source_parameter_unit=unit_c,
        )
        self.link.get_variable_mappings = lambda: [mapping]

    def save(self, rows, start_date=None):
        source = self.plugin._process_file(
            self.link, "/data", FILE, FakeDecoder(rows), FakeClient(), start_date=start_date,
        )
        total, _, _ = self.plugin.save_records(self.link, source)
        return total

    def test_second_run_upserts_only_the_appended_row(self):
        self.assertEqual(self.save(self.rows), 5)

        start = self.local(self.first + timedelta(hours=4))
        self.assertEqual(self.save(hourly_rows(self.first, 6), start_date=start), 1)

        self.assertEqual(ObservationRecord.objects.count(), 6)
        db_file = FTPStationDataFile.objects.get(station_link=self.link, file_name=FILE)
        self.assertEqual(db_file.values_saved, 1)
