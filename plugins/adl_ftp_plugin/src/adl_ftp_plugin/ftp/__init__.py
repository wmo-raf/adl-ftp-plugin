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
    
    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status
    
    def __str__(self):
        return f"{self.message} (HTTP {self.status})"


def _make_tls_conn(host, user, password, timeout, ctx, **kwargs):
    """
    Create an FTP_TLS connection with SSL session reuse on the data channel.
    This fixes the [SSL: SHUTDOWN_WHILE_IN_INIT] error that occurs when the
    server requires the data connection to reuse the control channel SSL session.
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
    
    return _FTP_TLS(host=host, user=user, passwd=password, timeout=timeout, context=ctx, **kwargs)


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
                self.conn = _make_tls_conn(host, user, password, timeout, ctx, **kwargs)
                if port:
                    self.conn.port = port
                self.conn.prot_p()
            else:
                self.conn = FTP(host=host, user=user, passwd=password, timeout=timeout, **kwargs)
                if port:
                    self.conn.port = port
            
            if not passive:
                self.conn.set_pasv(False)
        
        except FTP_CONNECTION_ERRORS as e:
            message, status = map_ftp_error(e)
            raise FTPError(message, status)
    
    def get(self, path, local=None):
        if isinstance(local, IOBase):  # open file, leave open
            local_file = local
        elif local is None:  # return string
            local_file = BytesIO()
        else:  # path to file, open, write/close return None
            local_file = open(local, 'wb')
        
        self.conn.retrbinary('RETR ' + path, local_file.write)
        
        if isinstance(local, IOBase):
            pass
        elif local is None:
            contents = local_file.getvalue()
            local_file.close()
            return contents
        else:
            local_file.close()
        
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
        except:
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
    
    def pwd(self):
        """ Return the current working directory """
        return self.conn.pwd()
    
    def list(self, remote='.', extra=False, remove_relative_paths=False):
        """ Return directory list """
        if extra:
            self.tmp_output = []
            self.conn.dir(remote, self._collector)
            directory_list = split_file_info(self.tmp_output)
        else:
            directory_list = self.conn.nlst(remote)
        
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


def map_ftp_error(exc):
    """Translate concrete exceptions to message + HTTP code."""
    if isinstance(exc, (socket.gaierror, socket.herror, ConnectionRefusedError)):
        return "Unable to reach FTP host", 502
    if isinstance(exc, socket.timeout):
        return "FTP connection timed out", 504
    if isinstance(exc, ssl.SSLError):
        return "TLS handshake with FTP server failed", 502
    if isinstance(exc, error_perm):
        if str(exc).startswith("530"):
            return "FTP Authentication failed", 401
        return "FTP permission error", 403
    if isinstance(exc, error_temp):
        return "FTP server temporarily unavailable", 503
    if isinstance(exc, error_reply):
        return "Unexpected reply from FTP server", 502
    # fallback
    return str(exc), 400
