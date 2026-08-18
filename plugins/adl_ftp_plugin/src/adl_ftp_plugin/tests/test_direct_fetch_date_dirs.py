"""
Which directory a DIRECT_FETCH filename is looked for in.

Under ``dir_structured_by_date`` the run used to generate the *whole* window's
filenames inside *every* date directory, so a backfill issued
``N_dirs x N_filenames`` RETR attempts of which all but ``N_filenames`` were
guaranteed misses — each one a failed round-trip, swallowed at debug level, so
the run merely crawled (wmo-raf/adl-ftp-plugin#9). A file belongs in exactly
one directory: the one its own timestamp names.

The grid is anchored on ``start_date``, not on each directory's boundary — the
filenames a run tries must not change depending on how the tree is carved up.

No database and no network: DIRECT_FETCH computes names from the clock alone.
"""

from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase

from adl_ftp_plugin.models import FTPListingStrategy
from adl_ftp_plugin.plugins import AdlFtpPlugin
from adl_ftp_plugin.tests.test_source_checks import make_station_link

UTC = dt_timezone.utc

BASE_KWARGS = {
    "ftp_path": "/data/station1",
    "file_pattern": None,
    "listing_strategy": FTPListingStrategy.DIRECT_FETCH,
    "direct_fetch_prefix": "STATION1_",
    "direct_fetch_datetime_format": "YYYYMMDDHHMM",
    "direct_fetch_datetime_timezone": "UTC",
    "direct_fetch_file_extension": ".txt",
    "direct_fetch_interval_minutes": 720,  # 12h, to keep the expected lists readable
    "use_connection_timezone": False,
    "timezone_info": "UTC",
}


def make_link(**overrides):
    kwargs = {**BASE_KWARGS, **overrides}
    return make_station_link(**kwargs)


def direct_fetch_paths(link, start, end):
    """The paths a run over ``[start, end]`` would try. DIRECT_FETCH needs
    neither a client nor a decoder, which is half of what is being pinned."""
    return list(AdlFtpPlugin()._get_file_paths(
        link, ftp_client=None, decoder=None, start_date=start, end_date=end
    ))


class DirectFetchDateDirectoryTests(SimpleTestCase):

    def paths(self, link, start, end):
        return direct_fetch_paths(link, start, end)

    def test_each_file_is_tried_only_in_the_directory_its_timestamp_names(self):
        link = make_link(dir_structured_by_date=True, date_granularity="day")
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 7, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/STATION1_202601050000.txt",
            "/data/station1/2026/01/05/STATION1_202601051200.txt",
            "/data/station1/2026/01/06/STATION1_202601060000.txt",
            "/data/station1/2026/01/06/STATION1_202601061200.txt",
            "/data/station1/2026/01/07/STATION1_202601070000.txt",
        ])

    def test_the_count_is_the_sum_over_directories_not_the_product(self):
        # The bug: days x (days x per-day) attempts. Five days at 12h spacing
        # is 11 files, not 55.
        link = make_link(dir_structured_by_date=True, date_granularity="day")
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(len(paths), 11)
        self.assertEqual(len(set(paths)), 11)  # and none of them tried twice

    def test_the_grid_stays_anchored_on_the_start_not_on_each_directory(self):
        # Starting at 06:00 the run tries 06:00 and 18:00 every day. Restarting
        # the grid at each directory boundary would ask for 00:00/12:00 from the
        # second day on — different files, none of which the station writes.
        link = make_link(dir_structured_by_date=True, date_granularity="day")
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 6, 0, tzinfo=UTC),
            datetime(2026, 1, 6, 18, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/STATION1_202601050600.txt",
            "/data/station1/2026/01/05/STATION1_202601051800.txt",
            "/data/station1/2026/01/06/STATION1_202601060600.txt",
            "/data/station1/2026/01/06/STATION1_202601061800.txt",
        ])

    def test_a_midnight_file_belongs_to_the_day_it_opens(self):
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         direct_fetch_interval_minutes=1440)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 6, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/STATION1_202601050000.txt",
            "/data/station1/2026/01/06/STATION1_202601060000.txt",
        ])

    def test_hour_granularity_carves_the_tree_down_to_the_hour(self):
        link = make_link(dir_structured_by_date=True, date_granularity="hour",
                         direct_fetch_interval_minutes=30)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
            datetime(2026, 1, 5, 23, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/22/STATION1_202601052200.txt",
            "/data/station1/2026/01/05/22/STATION1_202601052230.txt",
            "/data/station1/2026/01/05/23/STATION1_202601052300.txt",
        ])

    def test_month_granularity_uses_the_configured_month_directory_format(self):
        link = make_link(dir_structured_by_date=True, date_granularity="month",
                         month_dir_format="M", direct_fetch_interval_minutes=1440)
        paths = self.paths(
            link,
            datetime(2026, 1, 30, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/Jan/STATION1_202601300000.txt",
            "/data/station1/2026/Jan/STATION1_202601310000.txt",
            "/data/station1/2026/Feb/STATION1_202602010000.txt",
        ])

    def test_a_flat_tree_still_puts_every_file_under_the_root(self):
        link = make_link(dir_structured_by_date=False)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 6, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/STATION1_202601050000.txt",
            "/data/station1/STATION1_202601051200.txt",
            "/data/station1/STATION1_202601060000.txt",
        ])

    def test_granularity_unset_leaves_the_tree_flat(self):
        # dir_structured_by_date without a granularity says nothing about how
        # the tree is carved; the run must not invent one.
        link = make_link(dir_structured_by_date=True, date_granularity=None)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/STATION1_202601050000.txt",
            "/data/station1/STATION1_202601051200.txt",
        ])


class DirectFetchDirectoryTimezoneTests(SimpleTestCase):
    """Directories are named in the station timezone, filenames in the
    filename timezone. A file near a boundary must land in the directory its
    own instant falls in, not in whichever the two timezones disagree about."""

    def paths(self, link, start, end):
        return direct_fetch_paths(link, start, end)

    def test_the_directory_follows_the_station_timezone(self):
        # Station in UTC+3: 21:00 and 22:00 UTC on the 5th are already the 6th
        # locally, so they belong in the 6th's directory — while the filenames
        # stay in UTC.
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         timezone_info="Africa/Nairobi",
                         direct_fetch_interval_minutes=60)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 20, 0, tzinfo=UTC),
            datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/STATION1_202601052000.txt",
            "/data/station1/2026/01/06/STATION1_202601052100.txt",
            "/data/station1/2026/01/06/STATION1_202601052200.txt",
        ])

    def test_the_filename_follows_the_filename_timezone(self):
        # Both timezones in play at once: directory in UTC, names in UTC+3.
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         timezone_info="UTC",
                         direct_fetch_datetime_timezone="Africa/Nairobi",
                         direct_fetch_interval_minutes=60)
        paths = self.paths(
            link,
            datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
            datetime(2026, 1, 6, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/01/05/STATION1_202601060100.txt",
            "/data/station1/2026/01/05/STATION1_202601060200.txt",
            "/data/station1/2026/01/06/STATION1_202601060300.txt",
        ])


class DirectFetchDaylightSavingTests(SimpleTestCase):
    """The interval is a cadence in real time, so the walk steps in absolute
    instants and converts each one. Stepping a localized datetime instead
    carries the pre-transition offset across a DST change, which skews both
    the name and — since the same instant picks the directory — where the
    file is looked for."""

    def paths(self, link, start, end):
        return direct_fetch_paths(link, start, end)

    def test_spring_forward_skips_the_hour_that_does_not_exist(self):
        # 2026-03-08, US spring forward: 01:00 EST is followed an hour later
        # by 03:00 EDT. There is no 02:00 to ask for.
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         direct_fetch_datetime_timezone="America/New_York",
                         direct_fetch_interval_minutes=60)
        paths = self.paths(
            link,
            datetime(2026, 3, 8, 6, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/03/08/STATION1_202603080100.txt",
            "/data/station1/2026/03/08/STATION1_202603080300.txt",
            "/data/station1/2026/03/08/STATION1_202603080400.txt",
        ])

    def test_fall_back_repeats_the_hour_that_happens_twice(self):
        # 2025-11-02, US fall back: 01:00 EDT and 01:00 EST are an hour apart
        # and both real. Two distinct instants, one filename — the station
        # overwrites, and the run must not invent a 02:00 that is still an
        # hour away.
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         direct_fetch_datetime_timezone="America/New_York",
                         direct_fetch_interval_minutes=60)
        paths = self.paths(
            link,
            datetime(2025, 11, 2, 5, 0, tzinfo=UTC),
            datetime(2025, 11, 2, 7, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2025/11/02/STATION1_202511020100.txt",
            "/data/station1/2025/11/02/STATION1_202511020100.txt",
            "/data/station1/2025/11/02/STATION1_202511020200.txt",
        ])

    def test_the_directory_follows_the_stations_local_day_across_a_transition(self):
        # Station on New York time: the last file of local 8 March and the
        # first of the 9th, either side of local midnight, and the offset in
        # force is the post-transition one.
        link = make_link(dir_structured_by_date=True, date_granularity="day",
                         timezone_info="America/New_York",
                         direct_fetch_interval_minutes=60)
        paths = self.paths(
            link,
            datetime(2026, 3, 9, 3, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
        )
        self.assertEqual(paths, [
            "/data/station1/2026/03/08/STATION1_202603090300.txt",
            "/data/station1/2026/03/09/STATION1_202603090400.txt",
        ])
