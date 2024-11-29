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
    # if date is None, set it to the current date
    if from_date is None:
        from_date = dj_timezone.now()
    
    # Get the current time in the timezone
    now = dj_timezone.localtime(None, timezone)
    
    # Get the start date in the timezone
    start_date = dj_timezone.localtime(from_date, timezone)
    
    # Get the difference in years, months, days, and hours
    years_diff = now.year - start_date.year
    months_diff = now.month - start_date.month
    days_diff = now.day - start_date.day
    hours_diff = now.hour - start_date.hour
    
    # Create a list of date paths
    date_paths = []
    
    # If the date granularity is year
    if date_granularity == "year":
        for i in range(years_diff + 1):
            date_paths.append(start_date + relativedelta(years=i))
    
    # If the date granularity is month
    elif date_granularity == "month":
        for i in range(years_diff + 1):
            for j in range(months_diff + 1):
                date_paths.append(start_date + relativedelta(years=i, months=j))
    
    # If the date granularity is day
    elif date_granularity == "day":
        for i in range(years_diff + 1):
            for j in range(months_diff + 1):
                for k in range(days_diff + 1):
                    date_paths.append(start_date + relativedelta(years=i, months=j, days=k))
    
    # If the date granularity is hour
    elif date_granularity == "hour":
        for i in range(years_diff + 1):
            for j in range(months_diff + 1):
                for k in range(days_diff + 1):
                    for l in range(hours_diff + 1):
                        date_paths.append(start_date + relativedelta(years=i, months=j, days=k, hours=l))
    
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
