"""
What "processed" means for an FTP file, and what happens to a file when the
run around it is cut short.

``_process_file`` used to stamp ``processed_at`` the moment a file's records
had been *yielded* — before core had written any of them. Core buffers records
up to its chunk size before upserting, so for a feed with one record per file a
run that died after N files left N green "Processed" badges and no data. These
tests pin the contract that closes that gap: the file is stamped only after
core has consumed the trailing ``FLUSH`` (i.e. after the upsert), the stamp
carries how many values were kept, and a soft time limit raised during a
download is not swallowed as "file not found".
"""

import tempfile
from types import SimpleNamespace

from adl.core.registries import FLUSH
from adl.core.tests.factories import (
    CelsiusUnitFactory,
    DataParameterFactory,
    StationFactory,
)
from adl.core.models import ObservationRecord
from celery.exceptions import SoftTimeLimitExceeded
from django.test import TestCase, override_settings
from datetime import datetime, timezone as dt_timezone

from adl_ftp_plugin.ftp import FTPError
from adl_ftp_plugin.models import FTPListingStrategy, FTPStationDataFile, FTPStationLink
from adl_ftp_plugin.plugins import AdlFtpPlugin
from adl_ftp_plugin.tests.test_source_checks import make_connection

UTC = dt_timezone.utc
MEDIA_TMP = tempfile.mkdtemp(prefix="adl-ftp-test-media-")


class FakeClient:
    """``get(remote, local)`` writes canned bytes, or raises what it was told to."""

    def __init__(self, error=None, content=b"KMD,001,202608170345\n"):
        self.error = error
        self.content = content
        self.gets = []

    def get(self, remote_path, local_path):
        self.gets.append(remote_path)
        if self.error is not None:
            raise self.error
        with open(local_path, "wb") as f:
            f.write(self.content)


class FakeDecoder:
    def __init__(self, records=None, error=None):
        self.records = records if records is not None else []
        self.error = error

    def decode(self, path):
        if self.error is not None:
            raise self.error
        return {"values": list(self.records)}


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcessFileTestCase(TestCase):
    def setUp(self):
        self.plugin = AdlFtpPlugin()
        self.connection = make_connection(
            plugin="adl_ftp_plugin", name="FTP", username="u", password="p"
        )
        self.connection.network = StationFactory().network
        self.connection.save()
        self.link = FTPStationLink.objects.create(
            network_connection=self.connection,
            station=StationFactory(network=self.connection.network),
            ftp_path="/data",
            listing_strategy=FTPListingStrategy.DIRECT_FETCH,
            direct_fetch_prefix="KMD_001_",
            direct_fetch_datetime_format="YYYYMMDDHHMM",
            direct_fetch_interval_minutes=1,
            direct_fetch_datetime_timezone="UTC",
            direct_fetch_file_extension=".csv",
        )
        self.obs_time = datetime(2026, 8, 17, 3, 45, tzinfo=UTC)
        self.record = {"observation_time": self.obs_time, "air_temperature_2m": 21.5}

    def process(self, client, decoder, file_name="KMD_001_202608170345.csv"):
        return self.plugin._process_file(self.link, "/data", file_name, decoder, client)

    def db_file(self, file_name="KMD_001_202608170345.csv"):
        return FTPStationDataFile.objects.filter(station_link=self.link, file_name=file_name).first()


class SoftTimeLimitTests(ProcessFileTestCase):
    def test_soft_time_limit_during_download_propagates(self):
        # Direct fetch treats a failed download as "file not there yet" and
        # moves on. The batch soft limit is also an Exception, so it used to
        # be swallowed the same way — and the run rolled on to the hard kill.
        gen = self.process(FakeClient(error=SoftTimeLimitExceeded()), FakeDecoder([self.record]))
        with self.assertRaises(SoftTimeLimitExceeded):
            list(gen)
        self.assertIsNone(self.db_file())

    def test_a_missing_direct_fetch_file_is_still_skipped_quietly(self):
        gen = self.process(FakeClient(error=FTPError("not found", 550)), FakeDecoder([self.record]))
        self.assertEqual(list(gen), [])
        self.assertIsNone(self.db_file())


class ProcessedStampTests(ProcessFileTestCase):
    def test_file_is_stamped_only_after_core_consumed_the_flush(self):
        gen = self.process(FakeClient(), FakeDecoder([self.record]))

        first = next(gen)
        self.assertEqual(first, self.record)
        self.assertIsNone(self.db_file().processed_at, "stamped before core saw the records")

        marker = next(gen)
        self.assertIs(marker, FLUSH)
        # Core is now upserting; the generator has not been resumed yet
        self.assertIsNone(self.db_file().processed_at, "stamped before the upsert finished")

        # Core reports what it wrote for this chunk, then resumes the generator
        self.plugin.after_save_records(self.link, [self.record], ["obs-1", "obs-2", "obs-3"])
        with self.assertRaises(StopIteration):
            next(gen)

        db_file = self.db_file()
        self.assertIsNotNone(db_file.processed_at)
        self.assertEqual(db_file.values_saved, 3)

    def test_a_file_that_decodes_to_nothing_is_stamped_with_zero_values(self):
        items = list(self.process(FakeClient(), FakeDecoder([])))
        self.assertEqual(items, [FLUSH])
        db_file = self.db_file()
        self.assertIsNotNone(db_file.processed_at)
        self.assertEqual(db_file.values_saved, 0)

    def test_a_file_whose_values_core_all_dropped_is_stamped_with_zero_values(self):
        # after_save_records is called with an empty saved list (or not at
        # all, when nothing in the chunk mapped) — either way, 0, not None
        items = list(self.process(FakeClient(), FakeDecoder([self.record])))
        self.assertEqual(items, [self.record, FLUSH])
        self.assertEqual(self.db_file().values_saved, 0)

    def test_a_decode_failure_leaves_the_file_downloaded_but_unstamped(self):
        items = list(self.process(FakeClient(), FakeDecoder(error=ValueError("garbage"))))
        self.assertEqual(items, [])
        db_file = self.db_file()
        self.assertIsNotNone(db_file, "the download itself succeeded and is kept")
        self.assertIsNone(db_file.processed_at)
        self.assertIsNone(db_file.values_saved)

    def test_values_saved_counts_only_this_file(self):
        # A previous file's tally must not bleed into the next one
        self.link._adl_ftp_values_saved = 99
        gen = self.process(FakeClient(), FakeDecoder([self.record]))
        next(gen)  # record
        next(gen)  # FLUSH
        self.plugin.after_save_records(self.link, [self.record], ["obs-1"])
        with self.assertRaises(StopIteration):
            next(gen)
        self.assertEqual(self.db_file().values_saved, 1)


class EndToEndThroughCoreTests(ProcessFileTestCase):
    """The same generator driven by core's real save loop: the stamp on the
    file must equal the rows in ObservationRecord."""

    def setUp(self):
        super().setUp()
        unit_c = CelsiusUnitFactory()
        param = DataParameterFactory(name="air_temperature", unit=unit_c)
        mapping = SimpleNamespace(
            id=1, adl_parameter=param,
            source_parameter_name="air_temperature_2m", source_parameter_unit=unit_c,
        )
        self.link.get_variable_mappings = lambda: [mapping]

    def test_stamp_matches_persisted_rows_file_by_file(self):
        client = FakeClient()

        def source():
            yield from self.process(client, FakeDecoder([self.record]), "KMD_001_202608170345.csv")
            yield from self.process(client, FakeDecoder([]), "KMD_001_202608170346.csv")
            second = dict(self.record, observation_time=datetime(2026, 8, 17, 3, 47, tzinfo=UTC))
            yield from self.process(client, FakeDecoder([second]), "KMD_001_202608170347.csv")

        total, _, _ = self.plugin.save_records(self.link, source())

        self.assertEqual(total, 2)
        self.assertEqual(ObservationRecord.objects.count(), 2)
        self.assertEqual(self.db_file("KMD_001_202608170345.csv").values_saved, 1)
        self.assertEqual(self.db_file("KMD_001_202608170346.csv").values_saved, 0)
        self.assertEqual(self.db_file("KMD_001_202608170347.csv").values_saved, 1)

    def test_run_cut_short_keeps_earlier_files_data_and_leaves_the_rest_unstamped(self):
        # File 1 downloads and decodes; the download of file 2 hits the soft
        # limit. File 1's record must be in the DB and file 1 stamped; file 2
        # must be neither
        good, bad = FakeClient(), FakeClient(error=SoftTimeLimitExceeded())

        def source():
            yield from self.process(good, FakeDecoder([self.record]), "KMD_001_202608170345.csv")
            yield from self.process(bad, FakeDecoder([self.record]), "KMD_001_202608170346.csv")

        with self.assertRaises(SoftTimeLimitExceeded):
            self.plugin.save_records(self.link, source())

        self.assertEqual(ObservationRecord.objects.count(), 1)
        self.assertEqual(self.db_file("KMD_001_202608170345.csv").values_saved, 1)
        self.assertIsNone(self.db_file("KMD_001_202608170346.csv"))
