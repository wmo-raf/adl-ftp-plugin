import os
import posixpath
from datetime import datetime, timezone


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
    name_without_ext, ext = os.path.splitext(filename)
    
    # Handle edge case: hidden files with no extension
    if not name_without_ext:
        name_without_ext = filename
    
    # Map format to (expected_length, strptime_format)
    format_patterns = {
        # Compact formats
        "YYYYMMDD": (8, "%Y%m%d"),
        "YYYYMMDDHH": (10, "%Y%m%d%H"),
        "YYYYMMDDHHMM": (12, "%Y%m%d%H%M"),
        "YYYYMMDDHHMMSS": (14, "%Y%m%d%H%M%S"),
        "YYMMDD": (6, "%y%m%d"),
        "YYMMDDHHMM": (10, "%y%m%d%H%M"),
        "DDMMYYYY": (8, "%d%m%Y"),
        "MMDDYYYY": (8, "%m%d%Y"),
        "DDMMYY": (6, "%d%m%y"),
        "MMDDYY": (6, "%m%d%y"),
        
        # Dash separated
        "YYYY-MM-DD": (10, "%Y-%m-%d"),
        "YYYY-MM-DD-HH": (13, "%Y-%m-%d-%H"),
        "YYYY-MM-DD-HHMM": (15, "%Y-%m-%d-%H%M"),
        "YYYY-MM-DD-HHMMSS": (17, "%Y-%m-%d-%H%M%S"),
        "DD-MM-YYYY": (10, "%d-%m-%Y"),
        "MM-DD-YYYY": (10, "%m-%d-%Y"),
        "YY-MM-DD": (8, "%y-%m-%d"),
        
        # Underscore separated
        "YYYY_MM_DD": (10, "%Y_%m_%d"),
        "YYYY_MM_DD_HH": (13, "%Y_%m_%d_%H"),
        "YYYY_MM_DD_HHMM": (15, "%Y_%m_%d_%H%M"),
        "YYYY_MM_DD_HHMMSS": (17, "%Y_%m_%d_%H%M%S"),
        "DD_MM_YYYY": (10, "%d_%m_%Y"),
        "MM_DD_YYYY": (10, "%m_%d_%Y"),
        
        # Dot separated
        "YYYY.MM.DD": (10, "%Y.%m.%d"),
        "DD.MM.YYYY": (10, "%d.%m.%Y"),
        "MM.DD.YYYY": (10, "%m.%d.%Y"),
        
        # ISO 8601 / RFC 3339 variants
        "YYYY-MM-DDTHH": (13, "%Y-%m-%dT%H"),
        "YYYY-MM-DDTHHMMSS": (17, "%Y-%m-%dT%H%M%S"),
        "YYYY-MM-DDTHH:MM:SS": (19, "%Y-%m-%dT%H:%M:%S"),
        
        # Julian date
        "YYYYDDD": (7, "%Y%j"),
        "YYDDD": (5, "%y%j"),
        
        # Year and month only
        "YYYYMM": (6, "%Y%m"),
        "YYYY-MM": (7, "%Y-%m"),
        "YYYY_MM": (7, "%Y_%m"),
        
        # Text month formats (abbreviated) - case insensitive
        "YYYY-MMM-DD": (11, "%Y-%b-%d"),
        "DD-MMM-YYYY": (11, "%d-%b-%Y"),
        "YYYYMMMDD": (9, "%Y%b%d"),
        "DDMMMYYYY": (9, "%d%b%Y"),
    }
    
    # Handle Unix timestamp separately
    if date_format == "TIMESTAMP":
        if len(name_without_ext) >= 10:
            timestamp_str = name_without_ext[-10:]
            try:
                timestamp = int(timestamp_str)
                return datetime.fromtimestamp(timestamp, tz=tz)
            except (ValueError, OSError):
                return None
        return None
    
    if date_format not in format_patterns:
        return None
    
    expected_length, strptime_format = format_patterns[date_format]
    
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
    time_formats = [
        "YYYYMMDDHH",
        "YYYYMMDDHHMM",
        "YYYYMMDDHHMMSS",
        "YYYY-MM-DD-HH",
        "YYYY-MM-DD-HHMM",
        "YYYY-MM-DD-HHMMSS",
        "YYYY_MM_DD_HH",
        "YYYY_MM_DD_HHMM",
        "YYYY_MM_DD_HHMMSS",
        "YYYY-MM-DDTHH",
        "YYYY-MM-DDTHHMMSS",
        "YYYY-MM-DDTHH:MM:SS",
        "YYMMDDHHMM",
        "TIMESTAMP"
    ]
    
    has_time_component = date_format in time_formats
    
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
