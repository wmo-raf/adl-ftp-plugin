from django.core.exceptions import ImproperlyConfigured
from wis2box_adl.core.registry import Registry, Instance


class FTPDecoder(Instance):
    """
    This abstract class represents a custom ftp data decoder that can be added to the registry.
    It must be extended so properties and methods can be added.
    """
    
    type = ""
    compat_type = ""
    
    def __init__(self):
        if not self.type:
            raise ImproperlyConfigured("The type of an instance must be set.")
    
    def decode(self, file_path):
        """
        Decodes the given file and returns the result.

        :param file_path: The data that should be decoded.
        :type file_path: str
        :return: The decoded data.
        :rtype: list[dict]
        """
        raise NotImplementedError


class FTPDecoderRegistry(Registry):
    """
    With the decoder registry it is possible to register new ftp data decoders.
    """
    
    name = "adl_ftp_decoder"


ftp_decoder_registry = FTPDecoderRegistry()