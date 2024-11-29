from ftplib import FTP, error_perm
from io import IOBase, BytesIO

from .utils import split_file_info


class FTPClient:
    """ FTP client """
    tmp_output = None
    relative_paths = {'.', '..'}
    
    def __init__(self, host, port, user, password, secure=False, passive=True, ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        if port:
            FTP.port = port
        self.conn = FTP(host=host, user=user, passwd=password)
        
        if not passive:
            self.conn.set_pasv(False)
    
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
    
    def close(self):
        """ End the session """
        try:
            self.conn.quit()
        except Exception:
            self.conn.close()
