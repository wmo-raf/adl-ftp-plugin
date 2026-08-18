"""
Tests for the ingestion-diagnostic contracts (wmo-raf/adl#188):
``get_source_endpoint()``, ``check_source()``, ``check_station_source()``
and the ``adl_sources_count`` duck-typed handover.

All tests run without a database: model instances are built unsaved and the
FTP/SFTP clients are stubbed, so the seam under test is exactly the contract
core consumes.
"""

import ast
import fnmatch
import os
from datetime import timedelta
from unittest import mock

from adl.core.models import Network, Station
from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
from django.test import SimpleTestCase
from django.utils import timezone as dj_timezone

from adl_ftp_plugin.ftp import FTPError
from adl_ftp_plugin.ftp.sftp import SFTPError
from adl_ftp_plugin.models import (
    ConnectionType,
    FTPListingStrategy,
    FTPStationLink,
    FTPUpload,
    NetworkFTP,
    SmartMetFTPUpload,
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


class FailAfterFirstListClient(FakeClient):
    """Lists once, then fails — the "call 4 of 7 raises" shape from the idiom
    table, where the count earned so far must survive."""

    def list(self, path, extra=False):
        if self.listed_paths:
            raise FTPError("FTP permission error", 403)
        return super().list(path, extra=extra)


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
        # The client now classifies DNS and refused precisely, but both are
        # layer-4 statements and core stamps this return layer 5 — so the
        # check still declines a category rather than contradicting itself
        # about which layer failed.
        connection = make_connection()
        for message, status in (("Could not resolve FTP host", 521),
                                ("FTP host refused the connection", 522)):
            with self.subTest(status=status):
                error = FTPError(message, status)
                with mock.patch.object(NetworkFTP, "get_client", side_effect=error):
                    result = self.check(connection)
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)
                self.assertIn(message, result.message)

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


class SourcesCountTests(SimpleTestCase):
    """``adl_sources_count`` is committed only from something the source
    actually told us, and only once it has told us. The listing seam is real
    here — stubbing it out is what let wmo-raf/adl-ftp-plugin#4 hide."""

    def make_link(self, **kwargs):
        link = make_station_link(**kwargs)
        link.station = Station(name="Station 1")
        link.network_connection.network = Network(name="FTP Network")
        link.skip_already_downloaded_files = False
        return link

    def collect(self, link, client, process_file=None, start_date=None):
        plugin = AdlFtpPlugin()
        decoder = mock.Mock()
        decoder.get_matching_files.side_effect = (
            lambda station_link, files, start, end: [
                f for f in files if fnmatch.fnmatch(f, station_link.file_pattern)
            ]
        )
        if process_file is None:
            def process_file(*args, **kwargs):
                return iter(())

        with mock.patch.object(NetworkFTP, "get_client", return_value=client), \
                mock.patch.object(AdlFtpPlugin, "_get_configured_decoder", return_value=decoder), \
                mock.patch.object(AdlFtpPlugin, "_process_file", side_effect=process_file):
            return list(plugin.get_station_data(link, start_date, None))

    def test_counts_the_files_a_listing_matched(self):
        link = self.make_link()
        client = FakeClient(files=["STATION1_a.dat", "STATION1_b.dat", "other.txt"])
        self.collect(link, client)
        self.assertEqual(link.adl_sources_count, 2)

    def test_a_listing_that_matched_nothing_reports_zero(self):
        # The source answered and its answer was empty — that is the honest
        # source-empty, and it is what layer 5 exists to see.
        link = self.make_link()
        self.collect(link, FakeClient(files=["unrelated.txt"]))
        self.assertEqual(link.adl_sources_count, 0)

    def test_a_failed_listing_leaves_the_count_unset(self):
        # wmo-raf/adl-ftp-plugin#4: a run whose LIST raised must not also
        # assert the source offered nothing. Core abstains on NULL, so every
        # failure path has a free correct answer.
        link = self.make_link()
        client = FakeClient(list_error=FTPError("FTP permission error", 403))
        with self.assertRaises(FTPError):
            self.collect(link, client)
        self.assertIsNone(getattr(link, "adl_sources_count", None))

    def test_a_failure_after_a_good_listing_keeps_the_count_it_earned(self):
        # Right bias: we did see the source offering data, so the row acquits
        # the source even though the run failed.
        link = self.make_link(dir_structured_by_date=True, date_granularity="day")
        client = FailAfterFirstListClient(files=["STATION1_a.dat", "STATION1_b.dat"])
        two_days_ago = dj_timezone.now() - timedelta(days=2)
        with self.assertRaises(FTPError):
            self.collect(link, client, start_date=two_days_ago)
        self.assertEqual(link.adl_sources_count, 2)

    def test_a_run_that_never_reached_a_listing_leaves_the_count_unset(self):
        # "Did not look" must stay distinguishable from "looked, found nothing"
        link = self.make_link()
        plugin = AdlFtpPlugin()
        with mock.patch.object(NetworkFTP, "get_client", return_value=FakeClient()), \
                mock.patch.object(AdlFtpPlugin, "_get_configured_decoder", return_value=None):
            records = list(plugin.get_station_data(link, None, None))
        self.assertEqual(records, [])
        self.assertIsNone(getattr(link, "adl_sources_count", None))

    def test_the_count_is_committed_before_any_decoding(self):
        # §6.1: source items are counted before mapping or conversion, so a
        # decode failure still acquits the source.
        link = self.make_link()

        def failing_process(*args, **kwargs):
            def gen():
                return iter(())
                yield  # pragma: no cover - generator marker
            return gen()

        self.collect(link, FakeClient(files=["STATION1_a.dat"]), process_file=failing_process)
        self.assertEqual(link.adl_sources_count, 1)


class DirectFetchSourcesCountTests(SimpleTestCase):
    """DIRECT_FETCH constructs filenames instead of listing, so the count
    cannot come from the file list — a constructed name is our guess, not the
    source's offer, and counting those makes every run acquit the source."""

    def make_link(self):
        link = make_station_link(
            file_pattern=None,
            listing_strategy=FTPListingStrategy.DIRECT_FETCH,
            direct_fetch_prefix="STATION1_",
            direct_fetch_file_extension=".txt",
            direct_fetch_datetime_format="YYYYMMDDHHMM",
            direct_fetch_interval_minutes=10,
        )
        link.station = Station(name="Station 1")
        link.network_connection.network = Network(name="FTP Network")
        return link

    def collect(self, link, outcomes):
        """``outcomes`` is one True/False per attempted file: was it in hand?"""
        plugin = AdlFtpPlugin()
        remaining = list(outcomes)

        def process_file(*args, **kwargs):
            in_hand = remaining.pop(0)

            def gen():
                return in_hand
                yield  # pragma: no cover - generator marker

            return gen()

        with mock.patch.object(NetworkFTP, "get_client", return_value=FakeClient()), \
                mock.patch.object(AdlFtpPlugin, "_get_configured_decoder", return_value=mock.Mock()), \
                mock.patch.object(AdlFtpPlugin, "_generate_direct_fetch_filenames",
                                  return_value=[f"STATION1_{i}.txt" for i in range(len(remaining))]), \
                mock.patch.object(AdlFtpPlugin, "_process_file", side_effect=process_file):
            return list(plugin.get_station_data(link, None, None))

    def test_counts_only_the_files_actually_in_hand(self):
        link = self.make_link()
        self.collect(link, [True, False, True])
        self.assertEqual(link.adl_sources_count, 2)

    def test_every_expected_file_absent_reports_zero(self):
        # The server answered "not there" for each — a dead console, which is
        # precisely the fault layer 5 should be able to name.
        link = self.make_link()
        self.collect(link, [False, False])
        self.assertEqual(link.adl_sources_count, 0)

    def test_no_attempt_at_all_leaves_the_count_unset(self):
        link = self.make_link()
        self.collect(link, [])
        self.assertIsNone(getattr(link, "adl_sources_count", None))


def make_upload(model=FTPUpload, **kwargs):
    kwargs.setdefault("host", "ftp.example.test")
    kwargs.setdefault("user", "adl")
    kwargs.setdefault("password", "secret")
    kwargs.setdefault("directory", "/upload")
    kwargs.setdefault("connection_type", ConnectionType.FTP)
    return model(**kwargs)


class DispatchTestConnectionTests(SimpleTestCase):
    """``test_connection()`` on the dispatch mixin: connect and authenticate,
    nothing else. The subject is that the destination answers us."""

    def check(self, upload, client=None, error=None):
        captured = {}

        def fake_get_client(**kwargs):
            captured.update(kwargs)
            if error is not None:
                raise error
            return client

        with mock.patch.object(type(upload), "get_client", side_effect=fake_get_client):
            result = upload.test_connection()
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"ok", "supported", "message", "latency_ms"})
        self.assertIsInstance(result["latency_ms"], int)
        return result, captured

    def test_success_reports_ok_and_closes_the_client(self):
        upload = make_upload()
        client = FakeClient()
        result, _kwargs = self.check(upload, client=client)
        self.assertTrue(result["ok"])
        self.assertTrue(result["supported"])
        self.assertTrue(client.closed)

    def test_success_message_names_host_port_user_and_the_untested_directory(self):
        upload = make_upload(port="2121")
        result, _kwargs = self.check(upload, client=FakeClient())
        self.assertIn("ftp.example.test", result["message"])
        self.assertIn("2121", result["message"])
        self.assertIn("adl", result["message"])
        # §9.1: write access is deliberately not probed, so the message
        # carries its own limit rather than implying more than it tested.
        self.assertIn("/upload", result["message"])
        self.assertIn("not tested", result["message"])

    def test_connect_only_nothing_listed_changed_or_written(self):
        upload = make_upload()
        client = FakeClient()
        self.check(upload, client=client)
        self.assertEqual(client.cd_paths, [])
        self.assertEqual(client.listed_paths, [])

    def test_probe_is_bounded(self):
        upload = make_upload()
        _result, kwargs = self.check(upload, client=FakeClient())
        self.assertEqual(kwargs.get("timeout"), 5)

    def test_sftp_probe_bounds_paramikos_own_timeouts_too(self):
        # SSHClient.connect(timeout=...) bounds only TCP and the handshake;
        # paramiko's banner and auth timeouts are independent and sum to ~65s.
        upload = make_upload(connection_type=ConnectionType.SFTP)
        _result, kwargs = self.check(upload, client=FakeClient())
        self.assertEqual(kwargs.get("timeout"), 5)
        self.assertEqual(kwargs.get("banner_timeout"), 5)
        self.assertEqual(kwargs.get("auth_timeout"), 5)
        self.assertEqual(kwargs.get("channel_timeout"), 5)

    def test_ftp_probe_sends_no_paramiko_only_arguments(self):
        upload = make_upload()
        _result, kwargs = self.check(upload, client=FakeClient())
        self.assertNotIn("banner_timeout", kwargs)

    def test_ftp_error_reports_not_ok_but_still_supported(self):
        upload = make_upload()
        error = FTPError("FTP Authentication failed", 401)
        result, _kwargs = self.check(upload, error=error)
        self.assertFalse(result["ok"])
        self.assertTrue(result["supported"])
        self.assertIn("Authentication failed", result["message"])

    def test_sftp_error_reports_not_ok(self):
        upload = make_upload(connection_type=ConnectionType.SFTP)
        error = SFTPError("SSH Authentication failed", 401)
        result, _kwargs = self.check(upload, error=error)
        self.assertFalse(result["ok"])
        self.assertTrue(result["supported"])

    def test_never_returns_a_source_check_result(self):
        # §9.4: failed_source_check_result() sits a few dozen lines above in
        # models.py and looks like the obvious helper; core rejects it.
        upload = make_upload()
        error = FTPError("FTP Authentication failed", 401)
        result, _kwargs = self.check(upload, error=error)
        self.assertNotIsInstance(result, SourceCheckResult)

    def test_our_own_bugs_propagate_rather_than_being_flattened(self):
        upload = make_upload()
        with mock.patch.object(FTPUpload, "get_client", side_effect=TypeError("our bug")):
            with self.assertRaises(TypeError):
                upload.test_connection()

    def test_both_channels_report_the_button_as_supported(self):
        # Both are `class XUpload(BaseFTPUpload, DispatchChannel)`, so the
        # mixin wins the MRO; reversing the bases would silently restore
        # core's "not supported for this channel type".
        for model in (FTPUpload, SmartMetFTPUpload):
            with self.subTest(model=model.__name__):
                upload = make_upload(model=model)
                result, _kwargs = self.check(upload, client=FakeClient())
                self.assertTrue(result["supported"])


class DispatchGetClientTests(SimpleTestCase):
    """The bound is added through the client factory, and its default
    preserves dispatch behaviour exactly."""

    def test_default_call_adds_no_timeout_so_dispatch_is_untouched(self):
        upload = make_upload()
        with mock.patch("adl_ftp_plugin.models.FTPClient") as client_cls:
            upload.get_client()
        _args, kwargs = client_cls.call_args
        self.assertNotIn("timeout", kwargs)
        self.assertEqual(kwargs["host"], "ftp.example.test")

    def test_explicit_timeout_is_forwarded(self):
        upload = make_upload()
        with mock.patch("adl_ftp_plugin.models.FTPClient") as client_cls:
            upload.get_client(timeout=5)
        _args, kwargs = client_cls.call_args
        self.assertEqual(kwargs["timeout"], 5)

    def test_sftp_extra_kwargs_are_forwarded(self):
        upload = make_upload(connection_type=ConnectionType.SFTP)
        with mock.patch("adl_ftp_plugin.models.SFTPClient") as client_cls:
            upload.get_client(timeout=5, banner_timeout=5)
        _args, kwargs = client_cls.call_args
        self.assertEqual(kwargs["banner_timeout"], 5)


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
