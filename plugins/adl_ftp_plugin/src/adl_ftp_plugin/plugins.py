import os
import tempfile
from datetime import datetime, timedelta
from typing import Iterator, Dict, Any, Optional

from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from adl_ftp_plugin.date_formats import get_format_definition
from .models import FTPStationDataFile, FTPListingStrategy
from .registries import ftp_decoder_registry
from .utils import (
    normalize_path,
    get_dates_to_now,
    get_date_paths,
)


class AdlFtpPlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"
    
    def get_urls(self):
        return []
    
    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)
    
    def _get_configured_decoder(self, network_conn_ftp):
        """
        Get and configure the decoder for the given network connection.
        Returns None if decoder is not found or misconfigured.
        """
        logger = self.get_logger()
        decoder_name = network_conn_ftp.decoder
        decoder = self.get_decoder(decoder_name)
        
        if not decoder:
            logger.error(f"Decoder {decoder_name} not found in decoder registry.")
            return None
        
        if decoder_name == "standard_csv":
            if not network_conn_ftp.csv_config:
                logger.error(f"Standard CSV decoder selected but no CSV configuration set.")
                return None
            decoder._config = network_conn_ftp.csv_config
        
        return decoder
    
    def get_default_start_date(self, station_link):
        start_date = dj_timezone.localtime(dj_timezone.now(), timezone=station_link.timezone)
        return start_date
    
    def get_station_data(
            self,
            station_link,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Generator that yields station records one at a time.
    
        This method yields records as they are decoded from FTP files,
        rather than accumulating them all in memory.
    
        :param station_link: The station link configuration
        :param start_date: Start date for data collection
        :param end_date: End date for data collection
        :yields: Individual observation records
        """
        logger = self.get_logger()
        
        network_conn_ftp = station_link.network_connection
        
        ftp_client = None
        
        try:
            ftp_client = network_conn_ftp.get_client()
            
            # Get and configure the decoder
            decoder = self._get_configured_decoder(network_conn_ftp)
            if not decoder:
                return
            
            net_ftp_name = network_conn_ftp.network.name
            station_name = station_link.station.name
            
            # Process each path — yield records as we go
            for file_path in self._get_file_paths(station_link, ftp_client, decoder, start_date, end_date):
                current_path = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                
                logger.debug(
                    f"Getting FTP data from '{net_ftp_name}' for "
                    f"station '{station_name}' from FTP path '{current_path}'"
                )
                
                yield from self._process_file(station_link, current_path, file_name, decoder, ftp_client)
        
        except Exception as e:
            logger.error(f"Error fetching FTP data: {e}")
            raise
        finally:
            if ftp_client:
                ftp_client.close()
    
    def _get_file_paths(
            self,
            station_link,
            ftp_client,
            decoder,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
    ) -> Iterator[str]:
        """
        Yields remote file paths to process based on the listing strategy.
        Does not download or decode anything.
        """
        logger = self.get_logger()
        strategy = station_link.listing_strategy
        
        path = station_link.ftp_path
        timezone = station_link.timezone
        
        # Build list of directory paths to process
        if station_link.dir_structured_by_date and station_link.date_granularity:
            dates = get_dates_to_now(
                date_granularity=station_link.date_granularity,
                timezone=timezone,
                from_date=start_date
            )
            paths = get_date_paths(path, dates, station_link.date_granularity, station_link.month_dir_format)
        else:
            paths = [path]
        
        for current_path in paths:
            if strategy == FTPListingStrategy.DIRECT_FETCH:
                # No listing needed — construct filenames directly
                filenames = self._generate_direct_fetch_filenames(station_link, start_date, end_date)
                for filename in filenames:
                    yield normalize_path(f"{current_path}/{filename}")
            else:
                # PATTERN_ONLY / FILTER_BY_DATE — requires directory listing
                if not station_link.file_pattern:
                    logger.error(
                        f"file_pattern is required for {strategy} strategy but is not set "
                        f"for station {station_link.station.name}. Skipping path {current_path}."
                    )
                    continue
                
                if not ftp_client.cd(current_path):
                    logger.warning(f"Path {current_path} not found")
                    continue
                
                ftp_files_list = ftp_client.list(current_path, extra=True)
                files_list = [file["name"] for file in ftp_files_list]
                
                if strategy == FTPListingStrategy.FILTER_BY_DATE:
                    matching_files = decoder.get_matching_files(
                        station_link, files_list, start_date, end_date
                    )
                else:
                    matching_files = decoder.get_matching_files(
                        station_link, files_list, None, None
                    )
                
                if not matching_files:
                    logger.debug(
                        f"No files found for station {station_link.station.name} "
                        f"matching pattern '{station_link.file_pattern}' in path {current_path}"
                    )
                    continue
                
                for file_name in matching_files:
                    yield normalize_path(f"{current_path}/{file_name}")
    
    def _process_path(self, station_link, path: str, decoder, ftp_client, start_date=None, end_date=None):
        for file_path in self._get_file_paths(station_link, ftp_client, decoder, start_date, end_date):
            current_path = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)
            
            yield from self._process_file(station_link, current_path, file_name, decoder, ftp_client)
    
    def _generate_direct_fetch_filenames(self, station_link, start_date, end_date):
        """
        Generate expected filenames for a date range based on the station link's
        direct fetch configuration — prefix, interval, timezone and extension.

        :param station_link: The station link configuration
        :param start_date: Start datetime (UTC)
        :param end_date: End datetime (UTC)
        :return: List of filenames
        """
        import pytz
        
        logger = self.get_logger()
        
        tz = station_link.direct_fetch_datetime_timezone
        if isinstance(tz, str):
            tz = pytz.timezone(tz)
        
        # Convert start/end to the filename timezone
        local_start = dj_timezone.localtime(start_date, tz)
        local_end = dj_timezone.localtime(end_date, tz)
        
        prefix = station_link.direct_fetch_prefix
        interval = station_link.direct_fetch_interval_minutes
        extension = station_link.direct_fetch_file_extension or ".txt"
        datetime_format = station_link.direct_fetch_datetime_format
        
        if not datetime_format:
            logger.error(
                f"direct_fetch_datetime_format is not set for station {station_link.station.name}. "
                f"Cannot construct filenames."
            )
            return []
        
        filenames = []
        current = local_start
        
        format_def = get_format_definition(station_link.direct_fetch_datetime_format)
        if not format_def:
            logger.error(f"Invalid datetime format for station {station_link.station.name}")
            return []
        
        while current <= local_end:
            datetime_str = current.strftime(format_def["strptime"])
            filename = f"{prefix}{datetime_str}{extension}"
            filenames.append(filename)
            current += timedelta(minutes=interval)
        
        return filenames
    
    def _process_file(self, station_link, path, file_name, decoder, ftp_client):
        """
        Generator that processes a single FTP file and yields its records.

        Downloads the file if needed, decodes it, and yields each record.
        Handles both fresh downloads and already-downloaded files.
        """
        logger = self.get_logger()
        
        # Check if file exists in database
        db_data_file = FTPStationDataFile.objects.filter(
            station_link=station_link,
            file_name=file_name
        ).first()
        
        # Download if new file OR re-download is enabled
        needs_download = not db_data_file or not station_link.skip_already_downloaded_files
        
        if needs_download:
            remote_file_path = normalize_path(f"{path}/{file_name}")
            
            with tempfile.NamedTemporaryFile(suffix=file_name, delete=False) as temp_file:
                temp_path = temp_file.name
                
                try:
                    logger.debug(f"Downloading file {file_name}..")
                    ftp_client.get(remote_file_path, temp_path)
                    
                    # Create OR update existing record
                    if not db_data_file:
                        db_data_file = FTPStationDataFile(
                            station_link=station_link,
                            file_name=file_name,
                        )
                    
                    with open(temp_path, 'rb') as f:
                        db_data_file.file.save(file_name, f)
                
                except Exception as e:
                    # For direct fetch, missing files are expected — log at debug level
                    if station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
                        logger.debug(f"File {file_name} not found on server (expected for direct fetch), skipping.")
                    else:
                        logger.error(f"Error downloading file {file_name}: {e}")
                    return
                
                finally:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        else:
            logger.debug(f"File {file_name} already downloaded, skipping download")
        
        if not db_data_file or not db_data_file.file:
            return
        
        # Decode and yield records (always, whether freshly downloaded or existing)
        try:
            data = decoder.decode(db_data_file.file.path)
            file_records = data.get("values", [])
            
            logger.debug(f"Decoded {len(file_records)} records from {file_name}")
            
            for record in file_records:
                yield record
            
            db_data_file.processed_at = dj_timezone.now()
            db_data_file.save(update_fields=['processed_at'])
        
        except Exception as e:
            logger.error(f"Error decoding file {file_name}: {e}")
    
    def get_station_file_paths(self, station_link) -> list:
        logger = self.get_logger()
        ftp_client = None
        try:
            start_date, end_date = self.get_dates_for_station(station_link)
            network_conn_ftp = station_link.network_connection
            decoder = self._get_configured_decoder(network_conn_ftp)
            if not decoder:
                return []
            ftp_client = network_conn_ftp.get_client()
            return list(self._get_file_paths(station_link, ftp_client, decoder, start_date, end_date))
        except Exception as e:
            logger.error(f"Error during dry run: {e}")
            raise
        finally:
            if ftp_client:
                ftp_client.close()
