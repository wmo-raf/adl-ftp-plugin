import fnmatch

from django.core.exceptions import ImproperlyConfigured

from adl.core.registry import Registry, Instance


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
    
    def pre_process(self, file_path):
        """
        This method is called before the decoding process.

        :param file_path: The path to the file that should be decoded.
        :type file_path: str
        """
        return file_path
    
    def decode(self, file_path):
        """
        Decodes the given file and returns the result.

        :param file_path: The data that should be decoded.
        :type file_path: str
        :return: The decoded data.
        :rtype: list[dict]
        """
        raise NotImplementedError
    
    def get_matching_files(self, station_link, files, start_date=None, end_date=None):
        """
        Returns a list of files that match the decoder.

        :param station_link: The station link that is used to collect the data.
        :type station_link: adl_ftp_plugin.models.FTPStationLink
        :param files: The list of files that should be checked.
        :type files: list[str]
        :return: The list of matching files.
        :rtype: list[str]
        """
        pattern = station_link.file_pattern
        
        # Filter files by pattern
        matching_files = [file for file in files if fnmatch.fnmatch(file, pattern)]
        
        return matching_files


class FTPDecoderRegistry(Registry):
    """
    With the decoder registry it is possible to register new ftp data decoders.
    """
    
    name = "adl_ftp_decoder"


ftp_decoder_registry = FTPDecoderRegistry()
