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


def add_date_info_to_path(path, date_info):
    # Extract year, month, and day from the date_info dictionary
    year = str(date_info.get("year")) if date_info.get("year") else None
    month = date_info.get("month")
    day = date_info.get("day")
    hour = date_info.get("hour")
    
    # Build the parts list based on the presence of year,month,day and hour
    parts = [str(year)]
    
    if year:
        if month:
            parts.append(f"{int(month):02}")
            if day:
                parts.append(f"{int(day):02}")
                if hour:
                    parts.append(f"{int(hour):02}")
    
    # Join the path and the parts
    return os.path.join(path, *filter(None, parts))


def get_dates_to_now(date_granularity, timezone=None, from_date=None):
    if from_date is None:
        from_date = dj_timezone.now()
    
    # Ensure correct timezone handling
    now = dj_timezone.localtime(dj_timezone.now(), timezone)
    start_date = dj_timezone.localtime(from_date, timezone)
    
    if start_date > now:
        raise ValueError("from_date cannot be in the future")
    
    date_paths = []
    current_date = start_date
    
    while current_date <= now:
        date_paths.append(current_date)
        if date_granularity == "year":
            current_date += relativedelta(years=1)
        elif date_granularity == "month":
            current_date += relativedelta(months=1)
        elif date_granularity == "day":
            current_date += relativedelta(days=1)
        elif date_granularity == "hour":
            current_date += relativedelta(hours=1)
        else:
            raise ValueError("Invalid date granularity. Choose 'year', 'month', 'day', or 'hour'.")
    
    return date_paths


def get_date_paths(root_path, dates, date_granularity, ):
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
        
        path = add_date_info_to_path(root_path, date_info)
        
        paths.append(path)
    
    return paths
