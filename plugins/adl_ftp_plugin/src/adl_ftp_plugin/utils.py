import os

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
    
    # Build the parts list based on the presence of year, month, and day
    parts = [str(year)]
    if year:
        if month:
            parts.append(f"{int(month):02}")
            if day:
                parts.append(f"{int(day):02}")
    
    # Join the path and the parts
    return os.path.join(path, *filter(None, parts))
