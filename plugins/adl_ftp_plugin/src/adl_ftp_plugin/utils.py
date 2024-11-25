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
