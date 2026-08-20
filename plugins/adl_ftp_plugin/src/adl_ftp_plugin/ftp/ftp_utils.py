import posixpath

# ``parse_date_from_filename`` and ``filter_files_by_date_range`` used to live
# here, under the transport package, though they only ever read filenames.
# They now live in :mod:`adl_ftp_plugin.file_matching`, where a consumer with
# no FTP client can import them; re-exported here because decoder plugins
# import them from this path.
from adl_ftp_plugin.file_matching import (  # noqa: F401
    filter_files_by_date_range,
    parse_date_from_filename,
)


def get_ftp_dir_list(ftp_client, remote_path="/", root_request=False):
    """
    Get a list of directories from the FTP server.

    :param ftp_client: An instance of FTPClient connected to the FTP server.
    :param remote_path: The path on the FTP server to list directories from.
    :param root_request: If True, return root as a single expandable node
                         without loading its children yet.
    :return: A list of directories with their names and paths.
    """

    # On initial load, just return root node — children load on expand
    if root_request:
        return [{
            "id": "/",
            "label": "/",
            "children": None  # None = not yet loaded, triggers lazy fetch on expand
        }]

    directories = []
    ftp_dir_list = ftp_client.list(remote=remote_path, extra=True, remove_relative_paths=True)

    for item in ftp_dir_list:
        if item.get("directory") == "d":
            name = item.get("name")
            full_path = posixpath.normpath(posixpath.join(remote_path, name))
            directories.append({
                "id": full_path,
                "label": full_path,
                "children": None  # expandable but not yet loaded
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
