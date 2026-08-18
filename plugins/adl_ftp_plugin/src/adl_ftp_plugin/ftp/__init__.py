import os
import socket
import ssl
from ftplib import FTP, error_perm, error_temp, error_reply
from io import IOBase, BytesIO

from .utils import split_file_info

try:
    from ftplib import FTP_TLS
except ImportError:
    FTP_TLS = None

# The port a blank `port` field means, matching NetworkFTP.effective_port.
DEFAULT_FTP_PORT = 21

FTP_CONNECTION_ERRORS = (
    socket.gaierror, socket.herror,  # DNS
    ConnectionRefusedError, socket.timeout,  # TCP
    ssl.SSLError,  # TLS (if you add it)
    error_temp, error_perm, error_reply,  # FTP status codes
    OSError, EOFError, ConnectionResetError,  # low-level I/O
    ValueError, TypeError  # bad args
)


class FTPError(Exception):
    """ Base class for FTP errors """

    def __init__(self, message, status, category=None, layer=None):
        super().__init__(message)
        self.message = message
        self.status = status
        if category is None:
            # Raised directly with a status rather than wrapped: the status
            # still carries whatever meaning the table gives it.
            category, layer = FTP_STATUS_CLASSIFICATION.get(status, (None, None))
        # Duck-typed classification core reads off the raised exception
        # (adl.core.classification). Carried forward from the exception this
        # one replaces, so wrapping never costs a classification: core knows
        # socket.gaierror and ssl.SSLError by type, and would classify them
        # precisely if we let them through untouched.
        self.adl_category = category
        self.adl_layer = layer

    def __str__(self):
        return f"{self.message} ({self.status})"


def _open_tls_session(host, port, user, password, timeout, ctx, **kwargs):
    """
    Open a logged-in FTP_TLS session with SSL session reuse on the data channel.
    This fixes the [SSL: SHUTDOWN_WHILE_IN_INIT] error that occurs when the
    server requires the data connection to reuse the control channel SSL session.

    Connect and login are explicit calls rather than constructor arguments:
    ``FTP_TLS`` takes no port, so passing ``host`` to the constructor would
    open the control connection — and perform the login — against port 21
    before a configured port could be applied (wmo-raf/adl-ftp-plugin#6).
    """

    class _FTP_TLS(FTP_TLS):
        def ntransfercmd(self, cmd, rest=None):
            conn, size = FTP.ntransfercmd(self, cmd, rest)
            if self._prot_p:
                conn = self.context.wrap_socket(
                    conn,
                    server_hostname=self.host,
                    session=self.sock.session  # reuse control channel SSL session
                )
            return conn, size

    conn = _FTP_TLS(timeout=timeout, context=ctx, **kwargs)
    conn.connect(host=host, port=port or DEFAULT_FTP_PORT)
    conn.login(user=user, passwd=password)
    return conn


class FTPClient:
    """ FTP client """
    tmp_output = None
    relative_paths = {'.', '..'}

    def __init__(self, host, port, user, password, secure=False, passive=True, timeout=20, **kwargs):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

        try:
            if secure and FTP_TLS:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self.conn = _open_tls_session(host, port, user, password, timeout, ctx, **kwargs)
                self.conn.prot_p()
            else:
                ftp = FTP()
                ftp.timeout = timeout
                ftp.connect(host=host, port=port or DEFAULT_FTP_PORT)
                ftp.login(user=user, passwd=password)
                self.conn = ftp

            if not passive:
                self.conn.set_pasv(False)

        except FTP_CONNECTION_ERRORS as e:
            raise ftp_error_from(e)

    def get(self, path, local=None):
        if isinstance(local, IOBase):
            local_file = local
        elif local is None:
            local_file = BytesIO()
        else:
            local_file = open(local, 'wb')

        try:
            self.conn.retrbinary('RETR ' + path, local_file.write)
        except FTP_CONNECTION_ERRORS as e:
            raise ftp_error_from(e)
        finally:
            if not isinstance(local, IOBase) and local is not None:
                local_file.close()

        if local is None:
            contents = local_file.getvalue()
            local_file.close()
            return contents

        return None

    def put(self, local, remote, contents=None, quiet=False):
        """ Puts a local file (or contents) on to the FTP server

            local can be:
                a string: path to inpit file
                a file: opened for reading
                None: contents are pushed
        """
        remote_dir = os.path.dirname(remote)
        remote_file = os.path.basename(local) \
            if remote.endswith('/') else os.path.basename(remote)

        if contents:
            # local is ignored if contents is set
            local_file = BytesIO(contents)
        elif isinstance(local, IOBase):
            local_file = local
        else:
            local_file = open(local, 'rb')

        if remote_dir:
            self.descend(remote_dir, force=True)

        size = 0
        try:
            self.conn.storbinary('STOR %s' % remote_file, local_file)
            size = self.conn.size(remote_file)
        except Exception:
            if not quiet:
                raise
        finally:
            local_file.close()
            if remote_dir:
                depth = len(remote_dir.split('/'))
                back = "/".join(['..' for d in range(depth)])
                self.conn.cwd(back)
        return size

    def cd(self, remote):
        """ Change working directory on server """
        try:
            self.conn.cwd(remote)
        except Exception:
            return False
        else:
            return self.pwd()

    def stat_file(self, path):
        """
        Does ``path`` exist on the server, and how big is it? One ``SIZE``
        round-trip, no transfer. Returns ``{"exists": bool, "size": int|None}``;
        a 550 reply means "no such file", anything else raises ``FTPError``.
        """
        try:
            # SIZE is only guaranteed in binary mode; ASCII-mode servers reject it
            self.conn.voidcmd("TYPE I")
            size = self.conn.size(path)
        except error_perm as e:
            if str(e).startswith("550"):
                return {"exists": False, "size": None}
            raise ftp_error_from(e)
        except FTP_CONNECTION_ERRORS as e:
            raise ftp_error_from(e)
        return {"exists": True, "size": size}

    def pwd(self):
        """ Return the current working directory """
        return self.conn.pwd()

    def list(self, remote='.', extra=False, remove_relative_paths=False):
        try:
            if extra:
                self.tmp_output = []
                self.conn.dir(remote, self._collector)
                directory_list = split_file_info(self.tmp_output)
            else:
                directory_list = self.conn.nlst(remote)
        except FTP_CONNECTION_ERRORS as e:
            raise ftp_error_from(e)

        if remove_relative_paths:
            return list(filter(self.is_not_relative_path, directory_list))
        return directory_list

    def _collector(self, line):
        """ Helper for collecting output from dir() """
        self.tmp_output.append(line)

    def is_not_relative_path(self, path):
        if isinstance(path, dict):
            return path.get('name') not in self.relative_paths
        else:
            return path not in self.relative_paths

    def descend(self, remote, force=False):
        """ Descend, possibly creating directories as needed """
        remote_dirs = remote.split('/')
        for directory in remote_dirs:
            try:
                self.conn.cwd(directory)
            except Exception:
                if force:
                    self.conn.mkd(directory)
                    self.conn.cwd(directory)
        return self.conn.pwd()

    def close(self):
        """ End the session """
        try:
            self.conn.quit()
        except Exception:
            self.conn.close()


# status -> (category, layer) for the codes this module raises. A category is
# claimed only where the cause is unambiguous; everything else declines, which
# leaves the row to core's read-time tier rather than guessing at write time.
# Layers: 4 = network path, 5 = source. None = declined.
FTP_STATUS_CLASSIFICATION = {
    401: ("AUTH_FAILED", 5),
    403: ("PERMISSION_DENIED", 5),
    404: ("PATH_NOT_FOUND", 5),
    502: ("PROTOCOL_ERROR", 5),
    503: ("PROTOCOL_ERROR", 5),
    # Client-observed: no server code, so the category is honest but the
    # layer is not knowable from the type alone.
    504: ("TCP_TIMEOUT", None),
    521: ("DNS_FAILURE", 4),
    522: ("TCP_REFUSED", 4),
    # A TLS error is usually handshake (4) but can be raised mid-read (5),
    # and the type cannot tell — core declines the layer here too.
    525: ("TLS_FAILURE", None),
}


def map_ftp_error(exc):
    """Translate concrete exceptions to message + status code.

    The three codes below used to share 502, which threw away exactly the
    distinctions core makes for free: a wrapper must never be less
    classifiable than what it wraps. Each now carries its own code, and
    :data:`FTP_STATUS_CLASSIFICATION` turns that back into the category and
    layer the original type would have earned.
    """
    if isinstance(exc, (socket.gaierror, socket.herror)):
        return "Could not resolve FTP host", 521
    if isinstance(exc, ConnectionRefusedError):
        return "FTP host refused the connection", 522
    if isinstance(exc, socket.timeout):
        return "FTP connection timed out", 504
    if isinstance(exc, ssl.SSLError):
        return "TLS handshake with FTP server failed", 525
    if isinstance(exc, error_perm):
        if str(exc).startswith("530"):
            return "FTP Authentication failed", 401
        return "FTP permission error", 403
    if isinstance(exc, error_temp):
        return "FTP server temporarily unavailable", 503
    if isinstance(exc, error_reply):
        return "Unexpected reply from FTP server", 502
    # fallback — an unrecognised type is our bug rather than the server's,
    # so it declines a category (400 is absent from the table above)
    return f"{type(exc).__name__}: {exc}", 400


def ftp_error_from(exc):
    """The FTPError replacing ``exc``, classified as ``exc`` would have been."""
    message, status = map_ftp_error(exc)
    category, layer = FTP_STATUS_CLASSIFICATION.get(status, (None, None))
    return FTPError(message, status, category=category, layer=layer)
