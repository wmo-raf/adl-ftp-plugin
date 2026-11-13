"""
Append mode must never lose rows already in the day's file (#51).

Only a definite "no such file" starts a fresh file. A failed read fails the
dispatch, so it is retried and the cursor does not move; a column added to
the mappings mid-day keeps the earlier rows; headerless (SmartMet) files are
read back by position.

No database, and no network: an in-memory client stands in for the server.
"""

from datetime import datetime, timedelta
from ftplib import error_temp
from io import BytesIO
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from adl_ftp_plugin.dispatchers.ftp import dispatch_to_ftp
from adl_ftp_plugin.ftp import FTPClient, FTPError

UTC = ZoneInfo("UTC")
DAY = datetime(2026, 11, 12, tzinfo=UTC)
PATH = "/out/0-20000-0-1/WIGOS_0-20000-0-1_20261112.csv"
SMARTMET_PATH = "/out/WIGOS_0-20000-0-1_20261112.csv"


class InMemoryServer:
    """The subset of FTPClient/SFTPClient that dispatch uses."""

    def __init__(self, files=None, stat_error=None, get_error=None):
        self.files = dict(files or {})
        self.stat_error = stat_error
        self.get_error = get_error
        self.uploads = []

    def stat_file(self, path):
        if self.stat_error:
            raise self.stat_error
        return {"exists": path in self.files, "size": None}

    def get(self, path, local=None):
        if self.get_error:
            raise self.get_error
        return self.files[path]

    def put(self, local, remote, contents=None, quiet=False):
        self.files[remote] = local.getvalue()
        self.uploads.append(remote)

    def close(self):
        pass

    def text(self, path):
        return self.files[path].decode()


def channel(server, params=("air_temperature",)):
    return SimpleNamespace(
        name="Test channel",
        timezone=UTC,
        directory="/out",
        write_mode="append",
        connection_type="ftp",
        get_parameter_mapping_values=lambda: list(params),
        get_client=lambda: server,
    )


def record(hour, station_id=7, wigos_id="0-20000-0-1", **values):
    return {
        "station_id": station_id,
        "wigos_id": wigos_id,
        "timestamp": DAY + timedelta(hours=hour),
        "values": values or {"air_temperature": 20.0 + hour},
    }


class AppendModeTests(SimpleTestCase):
    def test_no_file_yet_starts_a_new_file(self):
        server = InMemoryServer()

        dispatch_to_ftp(channel(server), [record(1)])

        self.assertEqual(
            server.text(PATH),
            "station_id,wigos_id,date,time,air_temperature\r\n"
            "7,0-20000-0-1,2026-11-12,01:00:00,21.0\r\n",
        )

    def test_next_run_appends_to_the_existing_file(self):
        server = InMemoryServer()

        dispatch_to_ftp(channel(server), [record(1)])
        dispatch_to_ftp(channel(server), [record(2)])

        self.assertEqual(
            server.text(PATH).splitlines()[1:],
            ["7,0-20000-0-1,2026-11-12,01:00:00,21.0",
             "7,0-20000-0-1,2026-11-12,02:00:00,22.0"],
        )

    def test_failed_read_fails_the_dispatch_and_keeps_the_file(self):
        server = InMemoryServer()
        dispatch_to_ftp(channel(server), [record(1)])
        before = server.files[PATH]
        server.get_error = FTPError("FTP connection timed out", 504)

        with self.assertRaises(FTPError):
            dispatch_to_ftp(channel(server), [record(2)])

        self.assertEqual(server.files[PATH], before)
        self.assertEqual(server.uploads, [PATH])  # only the first run's upload

    def test_failed_existence_check_fails_the_dispatch(self):
        server = InMemoryServer(stat_error=FTPError("FTP server temporarily unavailable", 503))

        with self.assertRaises(FTPError):
            dispatch_to_ftp(channel(server), [record(1)])

        self.assertEqual(server.uploads, [])

    def test_column_added_mid_day_keeps_the_earlier_rows(self):
        server = InMemoryServer()
        dispatch_to_ftp(channel(server), [record(1)])

        dispatch_to_ftp(
            channel(server, params=("air_temperature", "precipitation")),
            [record(2, air_temperature=22.0, precipitation=0.4)],
        )

        self.assertEqual(
            server.text(PATH).splitlines(),
            ["station_id,wigos_id,date,time,air_temperature,precipitation",
             "7,0-20000-0-1,2026-11-12,01:00:00,21.0,",
             "7,0-20000-0-1,2026-11-12,02:00:00,22.0,0.4"],
        )

    def test_unreadable_existing_file_fails_the_dispatch(self):
        server = InMemoryServer(files={PATH: b"not,our,format\n1,2,3\n"})

        with self.assertRaises(Exception):
            dispatch_to_ftp(channel(server), [record(1)])

        self.assertEqual(server.files[PATH], b"not,our,format\n1,2,3\n")


class SmartMetAppendTests(SimpleTestCase):
    """SmartMet files have no header and one timestamp column."""

    def send(self, server, records, params=("air_temperature",)):
        dispatch_to_ftp(channel(server, params), records, create_station_dir=False,
                        include_wigos_id=False, use_single_timestamp=True, include_header=False)

    def test_next_run_appends_to_the_headerless_file(self):
        server = InMemoryServer()

        self.send(server, [record(1)])
        self.send(server, [record(2)])

        self.assertEqual(
            server.text(SMARTMET_PATH).splitlines(),
            ["7,20261112T010000,21.0",
             "7,20261112T020000,22.0"],
        )

    def test_changed_column_count_restarts_the_file_with_a_warning(self):
        # Without a header, earlier values cannot be matched to new mappings
        server = InMemoryServer()
        self.send(server, [record(1)])

        with self.assertLogs("adl_ftp_plugin.dispatchers.ftp", level="WARNING"):
            self.send(server, [record(2, air_temperature=22.0, precipitation=0.4)],
                      params=("air_temperature", "precipitation"))

        self.assertEqual(server.text(SMARTMET_PATH).splitlines(), ["7,20261112T020000,22.0,0.4"])


class FTPClientGetTests(SimpleTestCase):
    """``get`` returns the file and lets failures through."""

    def make_client(self, retrbinary):
        with mock.patch("adl_ftp_plugin.ftp.FTP"):
            client = FTPClient("ftp.example.org", 21, "user", "pass")
        client.conn = mock.Mock()
        client.conn.retrbinary.side_effect = retrbinary
        return client

    def test_returns_the_file_contents(self):
        client = self.make_client(lambda cmd, write: write(b"a,b\n1,2\n"))

        self.assertEqual(client.get("/out/file.csv"), b"a,b\n1,2\n")

    def test_a_failed_transfer_raises(self):
        client = self.make_client(error_temp("421 Timeout"))

        with self.assertRaises(FTPError):
            client.get("/out/file.csv")

    def test_a_failed_transfer_to_a_local_file_raises(self):
        client = self.make_client(error_temp("421 Timeout"))

        with self.assertRaises(FTPError):
            client.get("/out/file.csv", BytesIO())
