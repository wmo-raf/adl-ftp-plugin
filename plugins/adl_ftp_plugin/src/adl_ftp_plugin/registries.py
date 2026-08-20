from adl.core.registry import Registry, Instance
from django.core.exceptions import ImproperlyConfigured

from .file_matching import match_files


class FTPDecoder(Instance):
    """
    This abstract class represents a custom ftp data decoder that can be added
    to the registry.
    It must be extended so properties and methods can be added.
    """

    type = ""
    compat_type = ""

    requires_config = False
    """
    Set by decoders that cannot decode without the connection's
    ``csv_config``. Resolution refuses such a decoder when the connection has
    none, and hands the config to :meth:`decode` (see
    ``adl_ftp_plugin.decoder_resolution``).
    """

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

    def decode(self, file_path, config=None):
        """
        Decodes the given file and returns the result.

        ``config`` is the configuration the caller wants this decode to use —
        only decoders setting :attr:`requires_config` are handed one, and a
        decoder that ignores it may leave the argument off its signature
        entirely.

        :param file_path: The data that should be decoded.
        :type file_path: str
        :param config: Configuration for this decode, or ``None``.
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
                "unit": "knot",            # required: pint symbol of the value
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

        Reads the matching configuration off the station link and hands it to
        :func:`adl_ftp_plugin.file_matching.match_files`; override this only
        to change *which* files a decoder wants, not how names are matched.

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

        # Only FILTER_BY_DATE reads dates out of filenames; the other
        # strategies want every name the pattern matches.
        filters_by_date = station_link.listing_strategy == FTPListingStrategy.FILTER_BY_DATE

        return match_files(
            files,
            station_link.file_pattern,
            filename_date_format=station_link.filename_date_format if filters_by_date else None,
            start_date=start_date,
            end_date=end_date,
            tz=station_link.filename_date_timezone,
        )


class FTPDecoderRegistry(Registry):
    """
    With the decoder registry it is possible to register new ftp data decoders.
    """

    name = "adl_ftp_decoder"


ftp_decoder_registry = FTPDecoderRegistry()
