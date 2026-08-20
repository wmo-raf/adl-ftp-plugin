"""
File matching as a transport-neutral function: given a list of names, a glob
and an optional filename-date window, which names does ADL want?

The FTP plugin reaches this logic through ``FTPDecoder.get_matching_files``,
which reads the fields off an ``FTPStationLink``. A consumer that has no FTP
station link — the ADL Agent plugin, which receives the same files by upload
— calls :func:`match_files` with plain values instead (wmo-raf/adl#270).
These tests pin both halves: the plain-value function, and the fact that the
FTP method still answers exactly what it answered before.

Pure computation — no database, no network, no FTP transport imports.
"""

import subprocess
import sys
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from adl_ftp_plugin.file_matching import (
    filter_files_by_date_range,
    match_files,
    parse_date_from_filename,
)
from adl_ftp_plugin.models import FTPListingStrategy
from adl_ftp_plugin.registries import FTPDecoder

UTC = dt_timezone.utc
NAIROBI = ZoneInfo("Africa/Nairobi")


class MatchFilesTests(SimpleTestCase):
    """:func:`match_files` — the glob, then the optional date window."""

    def test_keeps_only_pattern_matches_in_listing_order(self):
        files = ["ST1_20250115.dat", "other.txt", "ST1_20250116.dat", "ST1.log"]

        self.assertEqual(
            match_files(files, "ST1_*.dat"),
            ["ST1_20250115.dat", "ST1_20250116.dat"],
        )

    def test_without_a_date_format_the_window_is_ignored(self):
        """No format means no filename dates to compare — pattern only."""
        files = ["ST1_20200101.dat", "ST1_20250115.dat"]

        self.assertEqual(
            match_files(
                files,
                "ST1_*.dat",
                start_date=datetime(2025, 1, 15, tzinfo=UTC),
                end_date=datetime(2025, 1, 15, tzinfo=UTC),
            ),
            files,
        )

    def test_date_window_filters_pattern_matches(self):
        files = [
            "ST1_20250114.dat",
            "ST1_20250115.dat",
            "ST1_20250116.dat",
            "ST1_20250117.dat",
            "other_20250115.dat",
        ]

        self.assertEqual(
            match_files(
                files,
                "ST1_*.dat",
                filename_date_format="YYYYMMDD",
                start_date=datetime(2025, 1, 15, 23, 59, tzinfo=UTC),
                end_date=datetime(2025, 1, 16, 0, 1, tzinfo=UTC),
            ),
            ["ST1_20250115.dat", "ST1_20250116.dat"],
        )

    def test_files_whose_date_cannot_be_parsed_are_dropped(self):
        files = ["ST1_20250115.dat", "ST1_nodate.dat"]

        self.assertEqual(
            match_files(
                files,
                "ST1_*.dat",
                filename_date_format="YYYYMMDD",
                start_date=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            ["ST1_20250115.dat"],
        )

    def test_filename_dates_are_read_in_the_given_timezone(self):
        """
        ``ST1_202501160100`` is 2025-01-16 01:00 in Nairobi, i.e. 22:00 UTC on
        the 15th — inside a window that ends at 23:00 UTC on the 15th, outside
        the same window read as UTC.
        """
        files = ["ST1_202501160100.dat"]
        window = {
            "filename_date_format": "YYYYMMDDHHMM",
            "start_date": datetime(2025, 1, 15, 12, tzinfo=UTC),
            "end_date": datetime(2025, 1, 15, 23, tzinfo=UTC),
        }

        self.assertEqual(match_files(files, "ST1_*.dat", tz=NAIROBI, **window), files)
        self.assertEqual(match_files(files, "ST1_*.dat", tz=UTC, **window), [])

    def test_no_files_gives_no_matches(self):
        self.assertEqual(match_files([], "*.dat"), [])


class ParseDateFromFilenameTests(SimpleTestCase):
    """Reading the date a filename ends with, now importable without FTP transport."""

    def test_reads_the_date_at_the_end_of_the_basename(self):
        self.assertEqual(
            parse_date_from_filename("ST1_20250115.dat", "YYYYMMDD"),
            datetime(2025, 1, 15, tzinfo=UTC),
        )

    def test_unparseable_name_gives_none(self):
        self.assertIsNone(parse_date_from_filename("ST1_notadate.dat", "YYYYMMDD"))

    def test_unknown_format_gives_none(self):
        self.assertIsNone(parse_date_from_filename("ST1_20250115.dat", "NOSUCH"))


class FilterFilesByDateRangeTests(SimpleTestCase):
    """The window comparison, which a date-only format loosens to whole days."""

    def test_date_only_formats_compare_at_date_level(self):
        """
        A date-only filename carries no time, so a window that starts late on
        the same day still wants the day's file.
        """
        self.assertEqual(
            filter_files_by_date_range(
                ["ST1_20250115.dat"],
                "YYYYMMDD",
                start_date=datetime(2025, 1, 15, 23, 59, tzinfo=UTC),
            ),
            ["ST1_20250115.dat"],
        )


class FtpUtilsReExportTests(SimpleTestCase):
    """
    The functions moved out of the ``ftp`` transport package, but their old
    import path is public API for decoder plugins and keeps working.
    """

    def test_ftp_utils_re_exports_the_moved_functions(self):
        from adl_ftp_plugin.ftp import ftp_utils

        self.assertIs(ftp_utils.parse_date_from_filename, parse_date_from_filename)
        self.assertIs(ftp_utils.filter_files_by_date_range, filter_files_by_date_range)


class ImportPurityTests(SimpleTestCase):
    """
    A non-FTP consumer must be able to import the matching helpers and the
    decoder registry without loading the FTP transport package (ftplib,
    paramiko's neighbours, the client). Checked in a fresh interpreter,
    because this test process has long since imported everything.
    """

    def assertImportsWithoutFtpTransport(self, module):
        script = (
            "import sys\n"
            f"import {module}\n"
            "print('adl_ftp_plugin.ftp' in sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "False",
            f"importing {module} pulled in adl_ftp_plugin.ftp",
        )

    def test_file_matching_does_not_import_ftp_transport(self):
        self.assertImportsWithoutFtpTransport("adl_ftp_plugin.file_matching")

    def test_registries_does_not_import_ftp_transport(self):
        self.assertImportsWithoutFtpTransport("adl_ftp_plugin.registries")


class DecoderGetMatchingFilesTests(SimpleTestCase):
    """
    ``FTPDecoder.get_matching_files`` reads the station link's fields and
    delegates: same answers as before the extraction.
    """

    class Decoder(FTPDecoder):
        type = "test_decoder"

    def setUp(self):
        self.decoder = self.Decoder()

    def station_link(self, **kwargs):
        return SimpleNamespace(**{
            "file_pattern": "ST1_*.dat",
            "listing_strategy": FTPListingStrategy.PATTERN_ONLY,
            "filename_date_format": None,
            "filename_date_timezone": UTC,
            **kwargs,
        })

    def test_pattern_only_ignores_the_date_window(self):
        files = ["ST1_20200101.dat", "ST1_20250115.dat", "other.dat"]

        self.assertEqual(
            self.decoder.get_matching_files(
                self.station_link(filename_date_format="YYYYMMDD"),
                files,
                start_date=datetime(2025, 1, 15, tzinfo=UTC),
                end_date=datetime(2025, 1, 15, tzinfo=UTC),
            ),
            ["ST1_20200101.dat", "ST1_20250115.dat"],
        )

    def test_filter_by_date_applies_the_window(self):
        files = ["ST1_20250114.dat", "ST1_20250115.dat", "ST1_20250116.dat"]

        self.assertEqual(
            self.decoder.get_matching_files(
                self.station_link(
                    listing_strategy=FTPListingStrategy.FILTER_BY_DATE,
                    filename_date_format="YYYYMMDD",
                ),
                files,
                start_date=datetime(2025, 1, 15, tzinfo=UTC),
                end_date=datetime(2025, 1, 15, tzinfo=UTC),
            ),
            ["ST1_20250115.dat"],
        )

    def test_filter_by_date_without_a_format_falls_back_to_the_pattern(self):
        files = ["ST1_20250114.dat", "ST1_20250115.dat"]

        self.assertEqual(
            self.decoder.get_matching_files(
                self.station_link(
                    listing_strategy=FTPListingStrategy.FILTER_BY_DATE,
                ),
                files,
                start_date=datetime(2025, 1, 15, tzinfo=UTC),
                end_date=datetime(2025, 1, 15, tzinfo=UTC),
            ),
            files,
        )

    def test_filter_by_date_uses_the_links_filename_timezone(self):
        files = ["ST1_202501160100.dat"]
        link = self.station_link(
            listing_strategy=FTPListingStrategy.FILTER_BY_DATE,
            filename_date_format="YYYYMMDDHHMM",
            filename_date_timezone=NAIROBI,
        )

        self.assertEqual(
            self.decoder.get_matching_files(
                link,
                files,
                start_date=datetime(2025, 1, 15, 12, tzinfo=UTC),
                end_date=datetime(2025, 1, 15, 23, tzinfo=UTC),
            ),
            files,
        )
