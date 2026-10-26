"""
The tier-1 availability check (wmo-raf/adl-ftp-plugin#41): ``check_availability``
answers from stat calls or a listing, never a download.

No database and no network: model instances are built unsaved and the client is
a fake, so the seam under test is the contract core consumes.
"""

from datetime import datetime, timezone as dt_timezone
from unittest import mock

from adl.core.availability_checks import (
    AvailabilityCheckResult,
    TIER_LISTING,
    normalise_availability_check_result,
)
from adl.core.source_checks import SourceCheckStatus
from django.test import SimpleTestCase

from adl_ftp_plugin.ftp import FTPError
from adl_ftp_plugin.models import FTPListingStrategy, NetworkFTP
from adl_ftp_plugin.tests.test_source_checks import make_station_link

UTC = dt_timezone.utc


class FakeAvailabilityClient:
    """Answers stat and list from a set of remote paths; records every call so
    a test can prove nothing was downloaded."""

    def __init__(self, files=(), dirs=None):
        self.files = set(files)
        self.dirs = dirs  # None: derive from files
        self.stat_paths = []
        self.listed_paths = []
        self.get_calls = []
        self.closed = False

    def _dirs(self):
        if self.dirs is not None:
            return set(self.dirs)
        return {path.rsplit("/", 1)[0] for path in self.files}

    def stat_file(self, path):
        self.stat_paths.append(path)
        return {"exists": path in self.files, "size": 1 if path in self.files else None}

    def cd(self, path):
        return path if path in self._dirs() else False

    def list(self, path, extra=False):
        self.listed_paths.append(path)
        return [{"name": p.rsplit("/", 1)[1]} for p in self.files if p.rsplit("/", 1)[0] == path]

    def get(self, *args, **kwargs):
        self.get_calls.append(args)
        raise AssertionError("an availability check must not download")

    def close(self):
        self.closed = True


DIRECT_KWARGS = {
    "ftp_path": "/data/station1",
    "file_pattern": None,
    "listing_strategy": FTPListingStrategy.DIRECT_FETCH,
    "direct_fetch_prefix": "S1_",
    "direct_fetch_datetime_format": "YYYYMMDDHHMM",
    "direct_fetch_datetime_timezone": "UTC",
    "direct_fetch_file_extension": ".txt",
    "direct_fetch_interval_minutes": 60,
    "use_connection_timezone": False,
    "timezone_info": "UTC",
}

FILTER_KWARGS = {
    "ftp_path": "/data/station1",
    "file_pattern": "S1_*.csv",
    "listing_strategy": FTPListingStrategy.FILTER_BY_DATE,
    "filename_date_format": "YYYYMMDD",
    "filename_date_timezone": "UTC",
    "use_connection_timezone": False,
    "timezone_info": "UTC",
}


def run_check(link, client, start, end):
    with mock.patch.object(NetworkFTP, "get_client", return_value=client):
        result = link.check_availability(start, end)
    return result


def at(day, hour=0, minute=0):
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC)


class SupportedPropertyTests(SimpleTestCase):

    def test_supported_for_direct_fetch_and_filter_by_date_without_io(self):
        for kwargs in (DIRECT_KWARGS, FILTER_KWARGS):
            link = make_station_link(**kwargs)
            with mock.patch.object(NetworkFTP, "get_client", side_effect=AssertionError("I/O")):
                self.assertTrue(link.availability_check_supported)

    def test_not_supported_for_pattern_only_without_io(self):
        link = make_station_link()
        with mock.patch.object(NetworkFTP, "get_client", side_effect=AssertionError("I/O")):
            self.assertFalse(link.availability_check_supported)

    def test_pattern_only_check_returns_unsupported(self):
        link = make_station_link()
        result = run_check(link, FakeAvailabilityClient(), at(5), at(6))
        self.assertEqual(result.status, SourceCheckStatus.UNSUPPORTED)


class DirectFetchTests(SimpleTestCase):

    def test_existing_files_become_ranges_at_the_interval_and_missing_ones_none(self):
        link = make_station_link(**DIRECT_KWARGS)
        client = FakeAvailabilityClient(files={
            "/data/station1/S1_202601051000.txt",
            "/data/station1/S1_202601051200.txt",
        })
        result = run_check(link, client, at(5, 10), at(5, 13))

        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertEqual(
            [(r.start, r.end, r.count, r.label) for r in result.ranges],
            [
                (at(5, 10), at(5, 11), None, "S1_202601051000.txt"),
                (at(5, 12), at(5, 13), None, "S1_202601051200.txt"),
            ],
        )
        self.assertEqual(client.get_calls, [])
        self.assertTrue(client.closed)

    def test_window_end_is_exclusive(self):
        link = make_station_link(**DIRECT_KWARGS)
        client = FakeAvailabilityClient(files={"/data/station1/S1_202601051100.txt"})
        result = run_check(link, client, at(5, 10), at(5, 11))
        self.assertEqual(result.ranges, ())
        self.assertEqual(client.stat_paths, ["/data/station1/S1_202601051000.txt"])

    def test_date_directory_and_filename_timezone_are_honoured(self):
        # Filenames in UTC+3, tree named in the station timezone (UTC): the
        # file for 09:00 UTC is stamped 1200 and lives in the 05 directory.
        link = make_station_link(
            **{**DIRECT_KWARGS,
               "direct_fetch_datetime_timezone": "Africa/Nairobi",
               "dir_structured_by_date": True,
               "date_granularity": "day"},
        )
        path = "/data/station1/2026/01/05/S1_202601051200.txt"
        client = FakeAvailabilityClient(files={path})
        result = run_check(link, client, at(5, 9), at(5, 10))
        self.assertEqual(client.stat_paths, [path])
        self.assertEqual([r.label for r in result.ranges], ["S1_202601051200.txt"])

    def test_result_passes_core_normalisation(self):
        link = make_station_link(**DIRECT_KWARGS)
        client = FakeAvailabilityClient(files={"/data/station1/S1_202601051000.txt"})
        result = normalise_availability_check_result(
            run_check(link, client, at(5, 10), at(5, 12)), tier=TIER_LISTING)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertEqual(len(result.ranges), 1)

    def test_stops_with_a_timeout_failure_when_the_budget_is_spent(self):
        link = make_station_link(**DIRECT_KWARGS)
        client = FakeAvailabilityClient()
        with mock.patch("adl_ftp_plugin.availability.time.monotonic", side_effect=[0, 11, 11]):
            result = run_check(link, client, at(5, 10), at(5, 13))
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "TCP_TIMEOUT")
        self.assertEqual(client.stat_paths, [])


class FilterByDateTests(SimpleTestCase):

    def test_daily_files_in_the_window_are_24h_ranges_and_others_ignored(self):
        link = make_station_link(**FILTER_KWARGS)
        client = FakeAvailabilityClient(files={
            "/data/station1/S1_20260104.csv",
            "/data/station1/S1_20260105.csv",
            "/data/station1/S1_20260106.csv",
            "/data/station1/S1_20260105.txt",  # wrong pattern
            "/data/station1/S1_latest.csv",    # no parsable date
        })
        result = run_check(link, client, at(5, 6), at(5, 12))

        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertEqual(
            [(r.start, r.end, r.label) for r in result.ranges],
            [(at(5), at(6), "S1_20260105.csv")],
        )
        self.assertEqual(client.get_calls, [])

    def test_granularity_follows_the_filename_date_format(self):
        link = make_station_link(**{**FILTER_KWARGS, "filename_date_format": "YYYYMMDDHH"})
        client = FakeAvailabilityClient(files={"/data/station1/S1_2026010510.csv"})
        result = run_check(link, client, at(5, 9), at(5, 12))
        self.assertEqual(
            [(r.start, r.end) for r in result.ranges], [(at(5, 10), at(5, 11))])

    def test_filename_timezone_shifts_the_range(self):
        # A day stamped in UTC+3 starts at 21:00 UTC the day before.
        link = make_station_link(**{**FILTER_KWARGS, "filename_date_timezone": "Africa/Nairobi"})
        client = FakeAvailabilityClient(files={"/data/station1/S1_20260105.csv"})
        result = run_check(link, client, at(4, 22), at(4, 23))
        self.assertEqual(
            [(r.start, r.end) for r in result.ranges], [(at(4, 21), at(5, 21))])

    def test_date_structured_tree_lists_each_directory_and_tolerates_missing_ones(self):
        link = make_station_link(**{**FILTER_KWARGS,
                                    "dir_structured_by_date": True, "date_granularity": "day"})
        client = FakeAvailabilityClient(
            files={"/data/station1/2026/01/05/S1_20260105.csv"},
            dirs={"/data/station1/2026/01/05"},
        )
        result = run_check(link, client, at(5, 20), at(6, 4))

        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertEqual(len(result.ranges), 1)
        self.assertEqual(client.listed_paths, ["/data/station1/2026/01/05"])
        self.assertIn("/data/station1/2026/01/06", result.message)

    def test_missing_plain_directory_is_path_not_found(self):
        link = make_station_link(**FILTER_KWARGS)
        result = run_check(link, FakeAvailabilityClient(dirs=set()), at(5), at(6))
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PATH_NOT_FOUND")

    def test_message_carries_path_and_count(self):
        link = make_station_link(**FILTER_KWARGS)
        client = FakeAvailabilityClient(files={"/data/station1/S1_20260105.csv"})
        result = run_check(link, client, at(5), at(6))
        self.assertIn("/data/station1", result.message)
        self.assertIn("1 file(s)", result.message)


class FailureTests(SimpleTestCase):

    def test_connection_failure_is_failed_with_category_and_no_client_left_open(self):
        link = make_station_link(**DIRECT_KWARGS)
        error = FTPError("FTP Authentication failed", 401)
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = link.check_availability(at(5, 10), at(5, 11))
        self.assertIsInstance(result, AvailabilityCheckResult)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "AUTH_FAILED")

    def test_failure_mid_check_is_failed_and_closes_the_client(self):
        link = make_station_link(**FILTER_KWARGS)
        client = FakeAvailabilityClient(files={"/data/station1/S1_20260105.csv"})
        client.list = mock.Mock(side_effect=FTPError("FTP permission error", 403))
        result = run_check(link, client, at(5), at(6))
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PERMISSION_DENIED")
        self.assertTrue(client.closed)
