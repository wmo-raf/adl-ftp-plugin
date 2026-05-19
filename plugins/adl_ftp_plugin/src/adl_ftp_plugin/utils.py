import calendar
import os

from dateutil.relativedelta import relativedelta
from django.utils import timezone as dj_timezone

from .registries import ftp_decoder_registry


def get_ftp_decoder_choices():
    """
    Returns a list of tuples with the decoder type and its display name.
    
    :return: The list of choices.
    :rtype: list[tuple[str, str]]
    """
    
    choices = [(decoder.type, decoder.display_name) for decoder in ftp_decoder_registry.registry.values()]
    
    return choices


def normalize_path(path):
    """
    Normalizes the given path.
    
    :param str path: The path to normalize.
    :return: The normalized path.
    :rtype: str
    """
    
    path = os.path.normpath(path)
    
    if path.startswith("/"):
        path = '/' + path.lstrip('/')
    
    return path


def add_date_info_to_path(path, date_info, month_dir_format=None):
    if month_dir_format is None:
        month_dir_format = "m"
    
    # Extract year, month, and day from the date_info dictionary
    year = str(date_info.get("year")) if date_info.get("year") else None
    month = date_info.get("month")
    day = date_info.get("day")
    hour = date_info.get("hour")
    
    # Build the parts list based on the presence of year,month,day and hour
    parts = [str(year)]
    
    if year:
        if month is not None:
            month = int(month)
            month_dir = get_month_dir_formatted(month, month_dir_format)
            parts.append(month_dir)
            if day is not None:
                parts.append(f"{int(day):02}")
                if hour is not None:
                    parts.append(f"{int(hour):02}")
    
    # Join the path and the parts
    return os.path.join(path, *filter(None, parts))


def get_dates_to_now(
        date_granularity=None,
        timezone=None,
        from_date=None,
        as_string=False,
        str_format=None,
):
    if from_date is None:
        from_date = dj_timezone.now()
    
    if date_granularity is None:
        date_granularity = "day"
    
    valid_granularities = {"year", "month", "day", "hour"}
    
    if date_granularity not in valid_granularities:
        raise ValueError(
            "Invalid date granularity. "
            "Choose 'year', 'month', 'day', or 'hour'."
        )
    
    # Convert to requested timezone
    now = dj_timezone.localtime(dj_timezone.now(), timezone)
    start_date = dj_timezone.localtime(from_date, timezone)
    
    if start_date > now:
        raise ValueError("from_date cannot be in the future")
    
    # Normalize to the beginning of the granularity
    if date_granularity == "year":
        start_date = start_date.replace(
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    
    elif date_granularity == "month":
        start_date = start_date.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    
    elif date_granularity == "day":
        start_date = start_date.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    
    elif date_granularity == "hour":
        start_date = start_date.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
    
    dates_list = []
    current_date = start_date
    
    while current_date <= now:
        if as_string and str_format:
            dates_list.append(current_date.strftime(str_format))
        else:
            dates_list.append(current_date)
        
        if date_granularity == "year":
            current_date += relativedelta(years=1)
        
        elif date_granularity == "month":
            current_date += relativedelta(months=1)
        
        elif date_granularity == "day":
            current_date += relativedelta(days=1)
        
        elif date_granularity == "hour":
            current_date += relativedelta(hours=1)
    
    return dates_list


def get_date_paths(root_path, dates, date_granularity, month_dir_format=None):
    paths = []
    
    for date in dates:
        date_info = {}
        
        year = date.year
        month = date.month
        day = date.day
        
        if date_granularity == "year":
            date_info.update({"year": year})
        elif date_granularity == "month":
            date_info.update({"year": year, "month": month})
        elif date_granularity == "day":
            date_info.update({"year": year, "month": month, "day": day})
        elif date_granularity == "hour":
            date_info.update({"year": year, "month": month, "day": day, "hour": date.hour})
        
        path = add_date_info_to_path(root_path, date_info, month_dir_format)
        
        paths.append(path)
    
    return paths


def get_month_dir_formatted(month_int, month_dir_format):
    """
    Returns a string representation of the month based on the given format.

    Args:
        month_int (int): The month number (1–12).
        month_dir_format (str): The format code (e.g., "m", "n", "M", "b", "F", "f").

    Returns:
        str: The formatted month string.

    Raises:
        ValueError: If the month_int is out of range or format is invalid.
    """
    if not 1 <= month_int <= 12:
        raise ValueError("month_int must be between 1 and 12")
    
    format_map = {
        "m": f"{month_int:02d}",  # '01' to '12'
        "n": str(month_int),  # '1' to '12'
        "M": calendar.month_abbr[month_int],  # 'Jan'
        "b": calendar.month_abbr[month_int].lower(),  # 'jan'
        "F": calendar.month_name[month_int],  # 'January'
        "f": calendar.month_name[month_int].lower(),  # 'january'
    }
    
    if month_dir_format not in format_map:
        raise ValueError(f"Unsupported month_dir_format: {month_dir_format}")
    
    return format_map[month_dir_format]


def resolve_variable_mappings(connection_variable_mappings, station_variable_mappings):
    """
      Resolves variable mappings from connection and station variable mappings.
      Station mappings override connection mappings for the same parameter ID.
  
      :param connection_variable_mappings: List of mappings from the connection.
      :param station_variable_mappings: List of mappings from the station (has higher priority).
      :return: A dictionary mapping `adl_parameter_id` to the resolved VariableMapping.
      """
    
    connection_variable_mappings = connection_variable_mappings or []
    station_variable_mappings = station_variable_mappings or []
    
    resolved = {m.adl_parameter_id: m for m in connection_variable_mappings}
    resolved.update({m.adl_parameter_id: m for m in station_variable_mappings})
    
    return resolved
