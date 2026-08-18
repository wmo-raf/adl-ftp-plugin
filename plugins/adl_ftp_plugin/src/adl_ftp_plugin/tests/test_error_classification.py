"""
Exception stamping: the classification an ingestion failure carries into
core's activity log.

Core classifies a caught exception from a duck-typed ``adl_category`` /
``adl_layer`` on the exception, falling back to an MRO walk over the types it
knows by name. Both clients wrap every transport fault in their own error
type, which used to discard that second tier entirely — the rule these tests
pin is that **a wrapper must never be less classifiable than what it wraps**
(wmo-raf/adl#228, wmo-raf/adl-ftp-plugin#5).

No database, and no network: the mapping is a pure function of the exception.
"""

import socket
import ssl
from ftplib import error_perm, error_reply, error_temp

import paramiko
from adl.core.classification import classify_failure
from django.test import SimpleTestCase

from adl_ftp_plugin.ftp import FTPError, ftp_error_from
from adl_ftp_plugin.ftp.sftp import SFTPError, sftp_error_from
from adl_ftp_plugin.models import failed_source_check_result


class FtpWrapperClassificationTests(SimpleTestCase):

    def assertClassifies(self, exc, category, layer):
        wrapped = ftp_error_from(exc)
        self.assertIsInstance(wrapped, FTPError)
        self.assertEqual(classify_failure(wrapped), (category, layer))

    def test_dns_failure_survives_the_wrapper(self):
        self.assertClassifies(socket.gaierror("no such host"), "DNS_FAILURE", 4)

    def test_connection_refused_survives_the_wrapper(self):
        self.assertClassifies(ConnectionRefusedError("refused"), "TCP_REFUSED", 4)

    def test_tls_failure_survives_the_wrapper_with_the_layer_declined(self):
        # Handshake (4) or mid-read (5) — the type cannot tell, and core
        # declines the layer for ssl.SSLError too.
        self.assertClassifies(ssl.SSLError("handshake failed"), "TLS_FAILURE", None)

    def test_timeout_claims_no_layer(self):
        # Client-observed: nothing was sent by the server, so neither layer
        # can be claimed from the type alone.
        self.assertClassifies(socket.timeout("timed out"), "TCP_TIMEOUT", None)

    def test_auth_failure_is_layer_5(self):
        self.assertClassifies(error_perm("530 Login incorrect"), "AUTH_FAILED", 5)

    def test_permission_error_is_layer_5(self):
        self.assertClassifies(error_perm("550 Permission denied"), "PERMISSION_DENIED", 5)

    def test_temporary_server_fault_is_a_protocol_error(self):
        self.assertClassifies(error_temp("421 Service not available"), "PROTOCOL_ERROR", 5)

    def test_unexpected_reply_is_a_protocol_error(self):
        # 502 now means exactly one thing, which is what makes it claimable.
        self.assertClassifies(error_reply("bad reply"), "PROTOCOL_ERROR", 5)

    def test_an_unrecognised_type_declines_rather_than_guessing(self):
        # Our own bug, not the server's: a stamp here would blame the source
        # and permanently suppress core's read-time tier.
        self.assertClassifies(RuntimeError("our bug"), None, None)

    def test_the_wrapper_is_never_less_classifiable_than_what_it_wraps(self):
        # The rule itself, over every type core classifies by name.
        for exc in (socket.gaierror("x"), ConnectionRefusedError("x"), ssl.SSLError("x")):
            with self.subTest(exc=type(exc).__name__):
                original = classify_failure(exc)
                self.assertNotEqual(original, (None, None))
                self.assertEqual(classify_failure(ftp_error_from(exc)), original)

    def test_a_directly_raised_status_still_classifies(self):
        self.assertEqual(classify_failure(FTPError("not found", 404)), ("PATH_NOT_FOUND", 5))


class SftpWrapperClassificationTests(SimpleTestCase):

    def assertClassifies(self, exc, category, layer):
        wrapped = sftp_error_from(exc)
        self.assertIsInstance(wrapped, SFTPError)
        self.assertEqual(classify_failure(wrapped), (category, layer))

    def test_dns_failure_survives_the_wrapper(self):
        self.assertClassifies(socket.gaierror("no such host"), "DNS_FAILURE", 4)

    def test_connection_refused_survives_the_wrapper(self):
        self.assertClassifies(ConnectionRefusedError("refused"), "TCP_REFUSED", 4)

    def test_ssh_auth_failure_is_layer_5(self):
        self.assertClassifies(paramiko.AuthenticationException("bad creds"), "AUTH_FAILED", 5)

    def test_host_key_mismatch_declines(self):
        # No category in the vocabulary means "the host is not who it claims
        # to be", and AUTH_FAILED would be wrong: no credential was offered.
        exc = paramiko.SSHException("host key mismatch")
        self.assertClassifies(exc, None, None)

    def test_generic_ssh_error_declines(self):
        self.assertClassifies(paramiko.SSHException("protocol banner error"), None, None)

    def test_missing_path_classifies_when_raised_directly(self):
        self.assertEqual(classify_failure(SFTPError("gone", 404)), ("PATH_NOT_FOUND", 5))


class SourceCheckCategoryTests(SimpleTestCase):
    """check_source fills a slot core stamps layer 5 by construction, so the
    layer-4 categories the clients now produce must not reach it."""

    def assertSourceCheckCategory(self, exc, expected):
        result = failed_source_check_result(ftp_error_from(exc))
        self.assertEqual(result.category, expected)

    def test_dns_failure_is_not_reported_at_layer_5(self):
        self.assertSourceCheckCategory(socket.gaierror("no such host"), None)

    def test_connection_refused_is_not_reported_at_layer_5(self):
        self.assertSourceCheckCategory(ConnectionRefusedError("refused"), None)

    def test_tls_failure_is_not_reported_at_layer_5(self):
        self.assertSourceCheckCategory(ssl.SSLError("handshake"), None)

    def test_auth_failure_is_reported(self):
        self.assertSourceCheckCategory(error_perm("530 Login incorrect"), "AUTH_FAILED")

    def test_permission_denied_is_reported(self):
        self.assertSourceCheckCategory(error_perm("550 Permission denied"), "PERMISSION_DENIED")

    def test_post_connect_timeout_is_reported(self):
        # The legitimate exception: a read stall after connect is layer 5.
        self.assertSourceCheckCategory(socket.timeout("timed out"), "TCP_TIMEOUT")

    def test_unexpected_reply_is_reported_as_a_protocol_error(self):
        self.assertSourceCheckCategory(error_reply("bad reply"), "PROTOCOL_ERROR")
