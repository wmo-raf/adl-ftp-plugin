"""
Tests for the ingestion-diagnostic contracts (wmo-raf/adl#188):
``get_source_endpoint()``, ``check_source()``, ``check_station_source()``
and the ``adl_sources_count`` duck-typed handover.

All tests run without a database: model instances are built unsaved and the
FTP/SFTP clients are stubbed, so the seam under test is exactly the contract
core consumes.
"""

import ast
import os
from unittest import mock

from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
from django.test import SimpleTestCase
from django.utils import timezone as dj_timezone

from adl_ftp_plugin.ftp import FTPError
from adl_ftp_plugin.ftp.sftp import SFTPError
from adl_ftp_plugin.models import (
    ConnectionType,
    FTPListingStrategy,
    FTPStationLink,
    NetworkFTP,
)
from adl_ftp_plugin.plugins import AdlFtpPlugin


class FakeClient:
    """A stub FTP/SFTP client capturing read-only listing calls."""

    def __init__(self, cd_result=True, files=(), list_error=None):
        self.cd_result = cd_result
        self.files = [{"name": name} for name in files]
        self.list_error = list_error
        self.closed = False
        self.cd_paths = []
        self.listed_paths = []

    def cd(self, path):
        self.cd_paths.append(path)
        return path if self.cd_result else False

    def list(self, path, extra=False):
        self.listed_paths.append(path)
        if self.list_error is not None:
            raise self.list_error
        return self.files

    def close(self):
        self.closed = True


def make_connection(**kwargs):
    kwargs.setdefault("host", "ftp.example.test")
    kwargs.setdefault("connection_type", ConnectionType.FTP)
    return NetworkFTP(**kwargs)


def make_station_link(connection=None, **kwargs):
    kwargs.setdefault("ftp_path", "/data/station1")
    kwargs.setdefault("file_pattern", "STATION1_*.dat")
    kwargs.setdefault("listing_strategy", FTPListingStrategy.PATTERN_ONLY)
    link = FTPStationLink(**kwargs)
    link.network_connection = connection or make_connection()
    return link


class GetSourceEndpointTests(SimpleTestCase):

    def test_returns_host_and_default_port(self):
        connection = make_connection()
        self.assertEqual(connection.get_source_endpoint(), ("ftp.example.test", 21))

    def test_returns_explicit_port(self):
        connection = make_connection(port=2121)
        self.assertEqual(connection.get_source_endpoint(), ("ftp.example.test", 2121))

    def test_sftp_defaults_to_port_22(self):
        connection = make_connection(connection_type=ConnectionType.SFTP)
        self.assertEqual(connection.get_source_endpoint(), ("ftp.example.test", 22))


class CheckSourceTests(SimpleTestCase):

    def check(self, connection):
        result = connection.check_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result

    def test_success_connects_and_closes_without_writing(self):
        connection = make_connection()
        client = FakeClient()
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(connection)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("ftp.example.test", result.message)
        self.assertTrue(client.closed)
        # Read-only: connecting must not have listed, changed or written anything
        self.assertEqual(client.cd_paths, [])
        self.assertEqual(client.listed_paths, [])

    def test_bad_credentials_reports_auth_failed(self):
        error = FTPError("FTP Authentication failed", 401)
        connection = make_connection()
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = self.check(connection)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "AUTH_FAILED")
        self.assertIn("Authentication failed", result.message)

    def test_sftp_bad_credentials_reports_auth_failed(self):
        error = SFTPError("SSH Authentication failed", 401)
        connection = make_connection(connection_type=ConnectionType.SFTP)
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = self.check(connection)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "AUTH_FAILED")

    def test_unreachable_host_reports_failed_without_invented_category(self):
        # 502 collapses DNS, refused and TLS faults into one status, so the
        # check declines a category rather than guessing one
        error = FTPError("Unable to reach FTP host", 502)
        connection = make_connection()
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = self.check(connection)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertIsNone(result.category)
        self.assertIn("Unable to reach FTP host", result.message)

    def test_timeout_reports_tcp_timeout(self):
        error = FTPError("FTP connection timed out", 504)
        connection = make_connection()
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = self.check(connection)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "TCP_TIMEOUT")

    def test_survives_the_core_normaliser(self):
        from adl.core.source_checks import normalise_source_check_result
        connection = make_connection()
        with mock.patch.object(NetworkFTP, "get_client", return_value=FakeClient()):
            result = normalise_source_check_result(connection.check_source())
        self.assertEqual(result.status, SourceCheckStatus.OK)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import connection_implements_check_source
        self.assertTrue(connection_implements_check_source(make_connection()))


class CheckStationSourceTests(SimpleTestCase):

    def check(self, link):
        result = link.check_station_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result

    def test_reports_resolved_path_and_match_count(self):
        link = make_station_link()
        client = FakeClient(files=["STATION1_20260731.dat", "STATION1_20260730.dat", "other.txt"])
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("/data/station1", result.message)
        self.assertIn("2", result.message)
        self.assertTrue(client.closed)

    def test_zero_matches_is_ok(self):
        link = make_station_link()
        client = FakeClient(files=["unrelated.txt"])
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("0", result.message)
        self.assertIn("/data/station1", result.message)

    def test_date_structured_path_resolves_to_current_period(self):
        link = make_station_link(
            dir_structured_by_date=True,
            date_granularity="day",
            month_dir_format="m",
        )
        client = FakeClient(files=[])
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        now = dj_timezone.localtime(dj_timezone.now(), link.timezone)
        expected = os.path.join("/data/station1", str(now.year), f"{now.month:02}", f"{now.day:02}")
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn(expected, result.message)
        self.assertIn(expected, client.listed_paths)

    def test_missing_remote_path_reports_path_not_found(self):
        link = make_station_link()
        client = FakeClient(cd_result=False)
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PATH_NOT_FOUND")
        self.assertIn("/data/station1", result.message)
        self.assertTrue(client.closed)

    def test_bad_credentials_reports_auth_failed(self):
        link = make_station_link()
        error = FTPError("FTP Authentication failed", 401)
        with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "AUTH_FAILED")

    def test_listing_error_reports_failed_and_closes_client(self):
        link = make_station_link()
        client = FakeClient(list_error=FTPError("FTP permission error", 403))
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PERMISSION_DENIED")
        self.assertTrue(client.closed)

    def test_direct_fetch_counts_files_matching_prefix_and_extension(self):
        link = make_station_link(
            file_pattern=None,
            listing_strategy=FTPListingStrategy.DIRECT_FETCH,
            direct_fetch_prefix="STATION_001_",
            direct_fetch_file_extension=".txt",
        )
        client = FakeClient(files=["STATION_001_20260731.txt", "STATION_002_20260731.txt"])
        with mock.patch.object(NetworkFTP, "get_client", return_value=client):
            result = self.check(link)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("1", result.message)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import station_link_implements_check_station_source
        self.assertTrue(station_link_implements_check_station_source(make_station_link()))


class StubStationLink:
    """The minimal station-link surface get_station_data touches when the
    listing itself is stubbed out."""

    def __init__(self):
        self.network_connection = mock.Mock()
        self.station = mock.Mock()
        self.station.name = "Station 1"


class SourcesCountTests(SimpleTestCase):

    def collect(self, plugin, link, paths):
        with mock.patch.object(AdlFtpPlugin, "_get_configured_decoder", return_value=mock.Mock()), \
                mock.patch.object(AdlFtpPlugin, "_get_file_paths", return_value=iter(paths)), \
                mock.patch.object(AdlFtpPlugin, "_process_file", return_value=iter(())):
            return list(plugin.get_station_data(link))

    def test_counts_resolved_files(self):
        plugin, link = AdlFtpPlugin(), StubStationLink()
        self.collect(plugin, link, ["/data/a.dat", "/data/b.dat", "/data/c.dat"])
        self.assertEqual(link.adl_sources_count, 3)

    def test_zero_resolved_files_reports_zero_not_none(self):
        plugin, link = AdlFtpPlugin(), StubStationLink()
        self.collect(plugin, link, [])
        self.assertEqual(link.adl_sources_count, 0)

    def test_misconfigured_decoder_leaves_count_unset(self):
        # "Did not look" must stay distinguishable from "looked, found
        # nothing": a run that never reaches listing must not set the attribute
        plugin, link = AdlFtpPlugin(), StubStationLink()
        with mock.patch.object(AdlFtpPlugin, "_get_configured_decoder", return_value=None):
            records = list(plugin.get_station_data(link))
        self.assertEqual(records, [])
        self.assertFalse(hasattr(link, "adl_sources_count"))


class OlderCoreImportSafetyTests(SimpleTestCase):
    """The plugin must import cleanly on a core release that predates the
    source-check contracts, so nothing may import ``adl.core.source_checks``
    at module level."""

    MODULES = ["models.py", "plugins.py"]

    def test_no_module_level_import_of_source_checks(self):
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in self.MODULES:
            with open(os.path.join(package_dir, name)) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node.col_offset != 0:
                    continue  # indented imports are lazy, inside a function
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                self.assertNotIn("adl.core.source_checks", [module] + names,
                                 f"{name} imports adl.core.source_checks at module level")
