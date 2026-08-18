"""
Where the control connection actually dials, and in what order.

``FTPClient`` is shared between ingestion (``NetworkFTP.get_client()``) and
dispatch (``BaseFTPUpload.get_client()``), so a port that never reaches the
socket makes the ingestion diagnostic contradict itself: core's layer-4 probe
opens TCP to the configured port and passes, then ``check_source()`` builds a
client that dials 21 and fails, accusing the partner's server of refusing
credentials it never received (wmo-raf/adl-ftp-plugin#6).

The stub records the order of the calls, not just their final values: the bug
was precisely that host and credentials went to the *constructor*, which
connects and logs in before a port can be applied. A test that only asserted
"login happened" would pass against the broken code.

No database, and no network: the stubs record what they were called with and
nothing leaves the process.
"""

from unittest import mock

from django.test import SimpleTestCase

from adl_ftp_plugin.ftp import FTPClient


class _RecordingFTP:
    """Stands in for ``FTP``/``FTP_TLS`` and records the session opening."""

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.calls = []
        self.connected_to = None
        self.logged_in_as = None
        self.login_kwargs = None
        self.pasv = True

    def connect(self, host="", port=0, **kwargs):
        self.calls.append("connect")
        self.connected_to = (host, port)

    def login(self, user="", passwd="", **kwargs):
        self.calls.append("login")
        self.logged_in_as = (user, passwd)
        self.login_kwargs = kwargs

    def prot_p(self):
        self.calls.append("prot_p")

    def set_pasv(self, val):
        self.pasv = val


class FtpsControlConnectionTests(SimpleTestCase):
    """The FTPS branch must dial the configured port, not 21."""

    def _connect(self, **kwargs):
        with mock.patch("adl_ftp_plugin.ftp.FTP_TLS", _RecordingFTP):
            client = FTPClient(
                host="ftp.example.org", user="user", password="secret",
                secure=True, **kwargs
            )
        return client.conn

    def test_configured_port_reaches_the_control_connection(self):
        conn = self._connect(port=2121)
        self.assertEqual(conn.connected_to, ("ftp.example.org", 2121))

    def test_no_configured_port_falls_back_to_21(self):
        conn = self._connect(port=None)
        self.assertEqual(conn.connected_to, ("ftp.example.org", 21))

    def test_the_constructor_is_given_nothing_that_would_dial(self):
        # host/user/passwd in the constructor is the bug itself: ftplib
        # connects and logs in from __init__, before any port is applied.
        conn = self._connect(port=2121)
        self.assertEqual(conn.init_args, ())
        self.assertEqual(set(conn.init_kwargs), {"timeout", "context"})

    def test_login_follows_the_connection(self):
        conn = self._connect(port=2121)
        self.assertEqual(conn.calls, ["connect", "login", "prot_p"])
        self.assertEqual(conn.logged_in_as, ("user", "secret"))

    def test_auth_tls_is_left_to_run_before_the_login(self):
        # FTP_TLS.login() issues AUTH TLS itself unless told secure=False.
        # Suppressing it would send the credentials in the clear — the plain
        # sort of layer-5 fault this diagnostic is supposed to name honestly.
        conn = self._connect(port=2121)
        self.assertNotIn("secure", conn.login_kwargs)

    def test_the_timeout_still_reaches_the_session(self):
        # NetworkFTP's probe leans on this to bound a check_source() call.
        conn = self._connect(port=2121, timeout=7)
        self.assertEqual(conn.init_kwargs["timeout"], 7)

    def test_the_data_channel_is_still_protected(self):
        conn = self._connect(port=2121)
        self.assertIn("prot_p", conn.calls)

    def test_passive_mode_is_still_honoured(self):
        conn = self._connect(port=2121, passive=False)
        self.assertFalse(conn.pasv)


class PlainFtpControlConnectionTests(SimpleTestCase):
    """The plain branch already dialled correctly — pin it against regression."""

    def _connect(self, port):
        with mock.patch("adl_ftp_plugin.ftp.FTP", _RecordingFTP):
            client = FTPClient(
                host="ftp.example.org", port=port, user="user",
                password="secret", secure=False
            )
        return client.conn

    def test_configured_port_reaches_the_control_connection(self):
        self.assertEqual(self._connect(2121).connected_to, ("ftp.example.org", 2121))

    def test_no_configured_port_falls_back_to_21(self):
        self.assertEqual(self._connect(None).connected_to, ("ftp.example.org", 21))

    def test_login_follows_the_connection(self):
        self.assertEqual(self._connect(2121).calls, ["connect", "login"])
