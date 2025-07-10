import posixpath


def get_ftp_dir_list(ftp_client, remote_path="/"):
    """
    Get a list of directories from the FTP server.

    :param ftp_client: An instance of FTPClient connected to the FTP server.
    :param remote_path: The path on the FTP server to list directories from.
    :return: A list of directories with their names and paths.
    """
    directories = []
    ftp_dir_list = ftp_client.list(remote=remote_path, extra=True, remove_relative_paths=True)
    
    for item in ftp_dir_list:
        if item.get("directory") == "d":
            name = item.get("name")
            full_path = posixpath.normpath(posixpath.join(remote_path, name))
            directories.append({
                "id": full_path,
                "label": full_path,
                "children": None
            })
    
    return directories


def clean_remote_path(path, force_root=False):
    """
    Normalize and validate a remote path.
    """
    if force_root or path in {".", "..", "/"}:
        return "/"
    
    path = posixpath.normpath(path)
    
    if ".." in path.split("/"):
        return None  # Invalid
    if not path.startswith("/"):
        path = "/" + path
    return path
