import os
import posixpath
from datetime import datetime, timezone

from adl_ftp_plugin.date_formats import format_has_time_component, get_format_definition


def parse_date_from_filename(filename, date_format, tz=timezone.utc):
    """
    Extract and parse date from filename based on format.
    Assumes date is at the END of the filename (before extension).

    :param filename: The filename to parse
    :param date_format: The date format string (e.g., "YYYYMMDD")
    :param tz: Timezone for the parsed date
    :return: datetime object or None if date cannot be parsed
    """
    # Remove file extension
    name_without_ext, _ = os.path.splitext(filename)

    # Handle edge case: hidden files with no extension
    if not name_without_ext:
        name_without_ext = filename

    format_def = get_format_definition(date_format)
    if format_def is None:
        return None

    expected_length = format_def["length"]
    strptime_format = format_def["strptime"]

    # Handle Unix timestamp separately
    if date_format == "TIMESTAMP":
        if len(name_without_ext) >= expected_length:
            timestamp_str = name_without_ext[-expected_length:]
            try:
                timestamp = int(timestamp_str)
                return datetime.fromtimestamp(timestamp, tz=tz)
            except (ValueError, OSError):
                return None
        return None

    # Extract the last N characters based on expected length
    if len(name_without_ext) < expected_length:
        return None

    date_string = name_without_ext[-expected_length:]

    try:
        parsed_date = datetime.strptime(date_string, strptime_format)
        parsed_date = parsed_date.replace(tzinfo=tz)
        return parsed_date
    except ValueError:
        return None


def filter_files_by_date_range(files, date_format, start_date=None, end_date=None, tz=timezone.utc):
    """
    Filter files based on dates in their filenames.

    :param files: List of filenames
    :param date_format: Format of date in filename
    :param start_date: Minimum date (inclusive)
    :param end_date: Maximum date (inclusive)
    :param tz: Timezone for date parsing
    :return: List of filenames within the date range
    """

    # Determine if date format includes time component
    has_time_component = format_has_time_component(date_format)

    filtered_files = []

    for filename in files:
        file_date = parse_date_from_filename(filename, date_format, tz)

        if file_date is None:
            # Could not parse date from filename, skip it
            continue

        # For date-only formats, compare at date level
        if not has_time_component:
            if start_date:
                # Convert both to date-only for comparison
                file_date_only = file_date.date()
                start_date_only = start_date.date()

                if file_date_only < start_date_only:
                    continue

            if end_date:
                file_date_only = file_date.date()
                end_date_only = end_date.date()

                if file_date_only > end_date_only:
                    continue
        else:
            # For datetime formats, do precise datetime comparison
            if start_date and file_date < start_date:
                continue

            if end_date and file_date > end_date:
                continue

        filtered_files.append(filename)

    return filtered_files


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
