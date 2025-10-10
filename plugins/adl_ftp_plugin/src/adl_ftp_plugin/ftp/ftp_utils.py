import posixpath
import re
from datetime import datetime, timezone


def parse_date_from_filename(filename, date_format, tz=timezone.utc):
    """
    Extract and parse date from filename based on format.
    
    :param filename: The filename to parse
    :param date_format: The date format string (e.g., "YYYYMMDD")
    :param tz: Timezone for the parsed date
    :return: datetime object or None if date cannot be parsed
    """
    # Remove file extension
    name_without_ext = filename.rsplit('.', 1)[0]
    
    # Format patterns and their regex/strptime equivalents
    format_patterns = {
        # Compact formats
        "YYYYMMDD": (r'(\d{4})(\d{2})(\d{2})', "%Y%m%d"),
        "YYYYMMDDHH": (r'(\d{4})(\d{2})(\d{2})(\d{2})', "%Y%m%d%H"),
        "YYYYMMDDHHMM": (r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})', "%Y%m%d%H%M"),
        "YYYYMMDDHHMMSS": (r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})', "%Y%m%d%H%M%S"),
        "YYMMDD": (r'(\d{2})(\d{2})(\d{2})', "%y%m%d"),
        "YYMMDDHHMM": (r'(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})', "%y%m%d%H%M"),
        "DDMMYYYY": (r'(\d{2})(\d{2})(\d{4})', "%d%m%Y"),
        "MMDDYYYY": (r'(\d{2})(\d{2})(\d{4})', "%m%d%Y"),
        "DDMMYY": (r'(\d{2})(\d{2})(\d{2})', "%d%m%y"),
        "MMDDYY": (r'(\d{2})(\d{2})(\d{2})', "%m%d%y"),
        
        # Dash separated
        "YYYY-MM-DD": (r'(\d{4})-(\d{2})-(\d{2})', "%Y-%m-%d"),
        "YYYY-MM-DD-HH": (r'(\d{4})-(\d{2})-(\d{2})-(\d{2})', "%Y-%m-%d-%H"),
        "YYYY-MM-DD-HHMM": (r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', "%Y-%m-%d-%H%M"),
        "YYYY-MM-DD-HHMMSS": (r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})', "%Y-%m-%d-%H%M%S"),
        "DD-MM-YYYY": (r'(\d{2})-(\d{2})-(\d{4})', "%d-%m-%Y"),
        "MM-DD-YYYY": (r'(\d{2})-(\d{2})-(\d{4})', "%m-%d-%Y"),
        "YY-MM-DD": (r'(\d{2})-(\d{2})-(\d{2})', "%y-%m-%d"),
        
        # Underscore separated
        "YYYY_MM_DD": (r'(\d{4})_(\d{2})_(\d{2})', "%Y_%m_%d"),
        "YYYY_MM_DD_HH": (r'(\d{4})_(\d{2})_(\d{2})_(\d{2})', "%Y_%m_%d_%H"),
        "YYYY_MM_DD_HHMM": (r'(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})', "%Y_%m_%d_%H%M"),
        "YYYY_MM_DD_HHMMSS": (r'(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})(\d{2})', "%Y_%m_%d_%H%M%S"),
        "DD_MM_YYYY": (r'(\d{2})_(\d{2})_(\d{4})', "%d_%m_%Y"),
        "MM_DD_YYYY": (r'(\d{2})_(\d{2})_(\d{4})', "%m_%d_%Y"),
        
        # Dot separated
        "YYYY.MM.DD": (r'(\d{4})\.(\d{2})\.(\d{2})', "%Y.%m.%d"),
        "DD.MM.YYYY": (r'(\d{2})\.(\d{2})\.(\d{4})', "%d.%m.%Y"),
        "MM.DD.YYYY": (r'(\d{2})\.(\d{2})\.(\d{4})', "%m.%d.%Y"),
        
        # ISO 8601 / RFC 3339 variants
        "YYYY-MM-DDTHH": (r'(\d{4})-(\d{2})-(\d{2})T(\d{2})', "%Y-%m-%dT%H"),
        "YYYY-MM-DDTHHMMSS": (r'(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})(\d{2})', "%Y-%m-%dT%H%M%S"),
        "YYYY-MM-DDTHH:MM:SS": (r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})', "%Y-%m-%dT%H:%M:%S"),
        
        # Julian date
        "YYYYDDD": (r'(\d{4})(\d{3})', "%Y%j"),
        "YYDDD": (r'(\d{2})(\d{3})', "%y%j"),
        
        # Year and month only
        "YYYYMM": (r'(\d{4})(\d{2})', "%Y%m"),
        "YYYY-MM": (r'(\d{4})-(\d{2})', "%Y-%m"),
        "YYYY_MM": (r'(\d{4})_(\d{2})', "%Y_%m"),
        
        # Text month formats (abbreviated)
        "YYYY-MMM-DD": (r'(\d{4})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{2})', "%Y-%b-%d"),
        "DD-MMM-YYYY": (r'(\d{2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})', "%d-%b-%Y"),
        "YYYYMMMDD": (r'(\d{4})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{2})', "%Y%b%d"),
        "DDMMMYYYY": (r'(\d{2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})', "%d%b%Y"),
    }
    
    if date_format not in format_patterns:
        # Handle Unix timestamp separately
        if date_format == "TIMESTAMP":
            timestamp_match = re.search(r'(\d{10})', name_without_ext)
            if timestamp_match:
                try:
                    timestamp = int(timestamp_match.group(1))
                    return datetime.fromtimestamp(timestamp)
                except (ValueError, OSError):
                    return None
        return None
    
    regex_pattern, strptime_format = format_patterns[date_format]
    
    # Try to find the date pattern in the filename (searching from the end)
    matches = list(re.finditer(regex_pattern, name_without_ext))
    
    if not matches:
        return None
    
    # Get the last match (closest to file extension)
    last_match = matches[-1]
    date_string = last_match.group(0)
    
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
