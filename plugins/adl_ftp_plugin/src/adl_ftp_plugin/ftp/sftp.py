import os
import socket
import stat
from datetime import datetime
from io import IOBase, BytesIO

from .utils import dotdict

try:
    import paramiko
except ImportError:
    paramiko = None

SFTP_CONNECTION_ERRORS = (
    socket.gaierror, socket.herror,  # DNS
    ConnectionRefusedError, socket.timeout,  # TCP
    paramiko.AuthenticationException,  # SSH auth
    paramiko.SSHException,  # SSH general
    paramiko.BadHostKeyException,  # Host key
    paramiko.ChannelException,  # SFTP channel
    OSError, EOFError, ConnectionResetError,  # low-level I/O
    ValueError, TypeError  # bad args
)


class SFTPError(Exception):
    """ Base class for SFTP errors """
    
    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status
    
    def __str__(self):
        return f"{self.message} (HTTP {self.status})"


class SFTPClient:
    """ SFTP client using paramiko """
    relative_paths = {'.', '..'}
    
    def __init__(self, host, port=22, user=None, password=None, private_key=None,
                 timeout=20, host_key_policy='auto', look_for_keys=False, allow_agent=False, **kwargs):
        """
        Initialize SFTP client
        
        Args:
            host: SSH server hostname
            port: SSH port (default 22)
            user: Username for authentication
            password: Password for authentication
            private_key: Path to private key file or paramiko key object
            timeout: Connection timeout in seconds
            host_key_policy: 'auto', 'reject', or 'warn' for unknown host keys
        """
        if not paramiko:
            raise ImportError("paramiko library is required for SFTP connections")
        
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssh = None
        self.sftp = None
        
        try:
            # Create SSH client
            self.ssh = paramiko.SSHClient()
            
            # Set host key policy
            if host_key_policy == 'auto':
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            elif host_key_policy == 'warn':
                self.ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
            else:  # reject
                self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            
            # Handle private key
            pkey = None
            if private_key:
                if isinstance(private_key, str):
                    # Try to load private key from file
                    for key_class in [paramiko.RSAKey, paramiko.DSAKey,
                                      paramiko.ECDSAKey, paramiko.Ed25519Key]:
                        try:
                            pkey = key_class.from_private_key_file(private_key)
                            break
                        except:
                            continue
                else:
                    pkey = private_key
            
            # Connect via SSH
            self.ssh.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                pkey=pkey,
                timeout=timeout,
                look_for_keys=look_for_keys,
                allow_agent=allow_agent,
                **kwargs
            )
            
            # Open SFTP channel
            self.sftp = self.ssh.open_sftp()
        
        except SFTP_CONNECTION_ERRORS as e:
            message, status = map_sftp_error(e)
            raise SFTPError(message, status)
    
    def get(self, path, local=None):
        """
        Download a file from the remote server
        
        Args:
            path: Remote file path
            local: Local destination (file path, file object, or None for bytes)
            
        Returns:
            None if local is a path or file object, bytes if local is None
        """
        if isinstance(local, IOBase):  # open file, leave open
            self.sftp.getfo(path, local)
            return None
        elif local is None:  # return bytes
            local_file = BytesIO()
            self.sftp.getfo(path, local_file)
            contents = local_file.getvalue()
            local_file.close()
            return contents
        else:  # path to file, download directly
            self.sftp.get(path, local)
            return None
    
    def put(self, local, remote, contents=None, quiet=False):
        """
        Upload a file to the remote server
        
        Args:
            local: Local file path, file object, or None if using contents
            remote: Remote destination path
            contents: Bytes to upload (if local is None)
            quiet: If True, suppress exceptions
            
        Returns:
            File size on success, 0 on failure
        """
        size = 0
        local_file = None
        original_cwd = None
        
        try:
            # Store original working directory
            original_cwd = self.pwd()
            
            # Handle directory creation and navigation for absolute paths
            if remote.startswith('/'):
                # Absolute path - handle directory creation
                remote_dir = os.path.dirname(remote)
                remote_file = os.path.basename(remote)
                
                if remote_dir and remote_dir != '/':
                    # Change to root first for absolute paths
                    self.sftp.chdir('/')
                    # Create directory structure if needed
                    self._ensure_remote_dir(remote_dir, force=True, quiet=quiet)
                
                # Use the full remote path for upload
                upload_path = remote
            else:
                # Relative path - handle as before
                remote_dir = os.path.dirname(remote) if not remote.endswith('/') else remote.rstrip('/')
                remote_file = os.path.basename(remote) if not remote.endswith('/') else \
                    os.path.basename(local) if isinstance(local, str) else 'uploaded_file'
                
                if remote.endswith('/'):
                    upload_path = os.path.join(remote, remote_file).replace('\\', '/')
                else:
                    upload_path = remote
                
                if remote_dir:
                    self._ensure_remote_dir(remote_dir, force=True, quiet=quiet)
            
            # Prepare local file
            if contents:
                local_file = BytesIO(contents)
                self.sftp.putfo(local_file, upload_path)
            elif isinstance(local, IOBase):
                self.sftp.putfo(local, upload_path)
            else:
                self.sftp.put(local, upload_path)
            
            # Get file size
            try:
                size = self.sftp.stat(upload_path).st_size
            except:
                size = 0
        
        except Exception as e:
            if not quiet:
                raise
        finally:
            if local_file and contents:
                local_file.close()
            # Return to original directory
            if original_cwd:
                try:
                    self.sftp.chdir(original_cwd)
                except:
                    pass
        
        return size
    
    def _ensure_remote_dir(self, remote_dir, force=False, quiet=False):
        """
        Ensure remote directory exists, creating it if force=True
        Handles both absolute and relative paths properly
        """
        if not remote_dir or remote_dir == '/':
            return
        
        if remote_dir.startswith('/'):
            # Absolute path
            parts = [p for p in remote_dir.split('/') if p]
            current_path = ''
            
            for part in parts:
                current_path += '/' + part
                try:
                    self.sftp.stat(current_path)
                except:
                    if force:
                        try:
                            self.sftp.mkdir(current_path)
                        except Exception as e:
                            if not quiet:
                                raise SFTPError(f"Failed to create directory {current_path}: {str(e)}", 502)
                    else:
                        if not quiet:
                            raise SFTPError(f"Directory {current_path} does not exist", 404)
        else:
            # Relative path - use the original descend method
            self.descend(remote_dir, force=force)
    
    def cd(self, remote):
        """
        Change working directory on server
        
        Returns:
            Current directory path on success, False on failure
        """
        try:
            self.sftp.chdir(remote)
            return self.pwd()
        except Exception:
            return False
    
    def pwd(self):
        """Return the current working directory"""
        return self.sftp.getcwd() or '/'
    
    def list(self, remote='.', extra=False, remove_relative_paths=False):
        """
        Return directory listing
        
        Args:
            remote: Remote directory path
            extra: If True, return detailed file information compatible with split_file_info format
            remove_relative_paths: If True, filter out '.' and '..' entries
            
        Returns:
            List of filenames (extra=False) or list of file info dicts (extra=True)
        """
        try:
            if extra:
                # Get detailed listing in split_file_info compatible format
                directory_list = []
                attrs_list = self.sftp.listdir_attr(remote)
                
                for attr in attrs_list:
                    # Get file mode string
                    mode_str = stat.filemode(attr.st_mode) if attr.st_mode else '-rwxrwxrwx'
                    is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                    
                    # Get datetime info
                    if attr.st_mtime:
                        dt_obj = datetime.fromtimestamp(attr.st_mtime)
                        date_str = dt_obj.strftime('%b %d')
                        time_str = dt_obj.strftime('%H:%M')
                        year_str = dt_obj.strftime('%Y')
                    else:
                        dt_obj = datetime.now()
                        date_str = dt_obj.strftime('%b %d')
                        time_str = '00:00'
                        year_str = dt_obj.strftime('%Y')
                    
                    file_info = dotdict({
                        'directory': 'd' if is_dir else '-',
                        'flags': mode_str,
                        'perms': mode_str[1:],  # Remove first character (file type)
                        'items': '1',
                        'owner': str(attr.st_uid) if attr.st_uid else 'user',
                        'group': str(attr.st_gid) if attr.st_gid else 'group',
                        'size': attr.st_size or 0,
                        'date': date_str,
                        'time': time_str,
                        'year': year_str,
                        'name': attr.filename,
                        'datetime': dt_obj,
                        # Additional SFTP-specific info
                        'is_dir': is_dir,
                        'is_file': stat.S_ISREG(attr.st_mode) if attr.st_mode else False,
                    })
                    directory_list.append(file_info)
            else:
                # Simple listing
                directory_list = self.sftp.listdir(remote)
            
            # Filter relative paths if requested
            if remove_relative_paths:
                return list(filter(self.is_not_relative_path, directory_list))
            
            return directory_list
        
        except Exception as e:
            raise SFTPError(f"Failed to list directory {remote}: {str(e)}", 502)
    
    def is_not_relative_path(self, path):
        """Check if path is not a relative path (. or ..)"""
        if isinstance(path, dict):
            return path.get('name') not in self.relative_paths
        else:
            return path not in self.relative_paths
    
    def descend(self, remote, force=False):
        """
        Navigate to directory, creating directories if force=True
        
        Args:
            remote: Remote directory path (can be nested like 'dir1/dir2/dir3')
            force: If True, create directories that don't exist
            
        Returns:
            Current directory path
        """
        remote_dirs = [d for d in remote.split('/') if d]  # Filter empty strings
        
        for directory in remote_dirs:
            try:
                self.sftp.chdir(directory)
            except Exception:
                if force:
                    try:
                        self.sftp.mkdir(directory)
                        self.sftp.chdir(directory)
                    except Exception as e:
                        raise SFTPError(f"Failed to create/enter directory {directory}: {str(e)}", 502)
                else:
                    raise SFTPError(f"Directory {directory} does not exist", 404)
        
        return self.pwd()
    
    def mkdir(self, path, mode=0o755):
        """Create a directory"""
        try:
            self.sftp.mkdir(path, mode)
            return True
        except Exception as e:
            raise SFTPError(f"Failed to create directory {path}: {str(e)}", 502)
    
    def rmdir(self, path):
        """Remove a directory"""
        try:
            self.sftp.rmdir(path)
            return True
        except Exception as e:
            raise SFTPError(f"Failed to remove directory {path}: {str(e)}", 502)
    
    def remove(self, path):
        """Remove a file"""
        try:
            self.sftp.remove(path)
            return True
        except Exception as e:
            raise SFTPError(f"Failed to remove file {path}: {str(e)}", 502)
    
    def stat_file(self, path):
        """
        Does ``path`` exist on the server, and how big is it? One ``stat``
        round-trip. Returns ``{"exists": bool, "size": int|None}``; a missing
        file is a normal answer, any other failure raises ``SFTPError``.
        """
        try:
            attrs = self.sftp.stat(path)
        except FileNotFoundError:
            return {"exists": False, "size": None}
        except IOError as e:
            # paramiko raises IOError(errno=ENOENT) on older versions
            if getattr(e, "errno", None) == 2:
                return {"exists": False, "size": None}
            raise SFTPError(f"Failed to stat {path}: {e}", 502)
        except SFTP_CONNECTION_ERRORS as e:
            raise SFTPError(f"Failed to stat {path}: {e}", 502)
        return {"exists": True, "size": getattr(attrs, "st_size", None)}
    
    def stat(self, path):
        """Get file/directory statistics"""
        try:
            return self.sftp.stat(path)
        except Exception as e:
            raise SFTPError(f"Failed to stat {path}: {str(e)}", 404)
    
    def exists(self, path):
        """Check if a file or directory exists"""
        try:
            self.sftp.stat(path)
            return True
        except:
            return False
    
    def close(self):
        """Close the SFTP and SSH connections"""
        try:
            if self.sftp:
                self.sftp.close()
        except:
            pass
        try:
            if self.ssh:
                self.ssh.close()
        except:
            pass


def map_sftp_error(exc):
    """Translate concrete exceptions to message + HTTP code."""
    if isinstance(exc, (socket.gaierror, socket.herror, ConnectionRefusedError)):
        return "Unable to reach SSH host", 502
    if isinstance(exc, socket.timeout):
        return "SSH connection timed out", 504
    if isinstance(exc, paramiko.AuthenticationException):
        return "SSH Authentication failed", 401
    if isinstance(exc, paramiko.BadHostKeyException):
        return "SSH host key verification failed", 502
    if isinstance(exc, paramiko.SSHException):
        return f"SSH error: {str(exc)}", 502
    if isinstance(exc, paramiko.ChannelException):
        return f"SFTP channel error: {str(exc)}", 502
    # fallback
    return str(exc), 400
