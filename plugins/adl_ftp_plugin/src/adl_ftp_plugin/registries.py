import fnmatch

from adl.core.registry import Registry, Instance
from django.core.exceptions import ImproperlyConfigured

from .ftp.ftp_utils import filter_files_by_date_range


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
    
    def get_variables(self):
        """
        Declare the variables this decoder emits, so the admin can pre-populate
        a connection's variable mappings instead of having them typed by hand.

        Return a list of dicts, one per record key produced by ``decode()``::

            {
                "name": "wind_speed_2m",   # required: key emitted by decode()
                "unit": "knot",            # required: pint symbol of the value in the file
                "label": "Wind Speed 2m",  # optional: human name; defaults to name
                "adl_unit": "m/s",         # optional: pint symbol for an auto-created
                                           #   ADL DataParameter; defaults to unit
                "description": "...",      # optional
            }

        The default (empty list) means "unknown" and hides the populate action
        for connections using this decoder.

        :rtype: list[dict]
        """
        return []
    
    def get_matching_files(self, station_link, files, start_date=None, end_date=None):
        """
        Returns a list of files that match the decoder and date range.
    
        :param station_link: The station link that is used to collect the data.
        :type station_link: adl_ftp_plugin.models.FTPStationLink
        :param files: The list of files that should be checked.
        :type files: list[str]
        :param start_date: Start date for filtering
        :param end_date: End date for filtering
        :return: The list of matching files.
        :rtype: list[str]
        """
        from .models import FTPListingStrategy
        
        pattern = station_link.file_pattern
        
        # Filter files by pattern
        matching_files = [file for file in files if fnmatch.fnmatch(file, pattern)]
        
        # If strategy is filter_by_date, filter by date in filename
        if station_link.listing_strategy == FTPListingStrategy.FILTER_BY_DATE and station_link.filename_date_format:
            matching_files = filter_files_by_date_range(
                matching_files,
                station_link.filename_date_format,
                start_date,
                end_date,
                tz=station_link.filename_date_timezone
            )
        
        return matching_files


class FTPDecoderRegistry(Registry):
    """
    With the decoder registry it is possible to register new ftp data decoders.
    """
    
    name = "adl_ftp_decoder"


ftp_decoder_registry = FTPDecoderRegistry()
