import os
import tempfile
from datetime import datetime
from typing import Iterator, Dict, Any, Optional

from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from .models import FTPStationDataFile
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
        decoder_name = network_conn_ftp.decoder
        
        ftp_client = None
        
        try:
            ftp_client = network_conn_ftp.get_client()
            
            # get the decoder from the registry
            decoder = self.get_decoder(decoder_name)
            
            if not decoder:
                logger.error(f"Decoder {decoder_name} not found in decoder registry.")
                return
            
            if decoder_name == "standard_csv":
                if not network_conn_ftp.csv_config:
                    logger.error(f"Standard CSV decoder selected but no CSV configuration set.")
                    return
                decoder._config = network_conn_ftp.csv_config
            
            net_ftp_name = network_conn_ftp.network.name
            timezone = station_link.timezone
            station_name = station_link.station.name
            
            path = station_link.ftp_path
            
            # Build list of paths to process
            if station_link.dir_structured_by_date and station_link.date_granularity:
                date_granularity = station_link.date_granularity
                month_dir_format = station_link.month_dir_format
                
                dates = get_dates_to_now(
                    date_granularity=date_granularity,
                    timezone=timezone,
                    from_date=start_date
                )
                
                paths = get_date_paths(path, dates, date_granularity, month_dir_format)
            else:
                paths = [path]
            
            # Process each path - yield records as we go
            for current_path in paths:
                logger.debug(
                    f"Getting FTP data from '{net_ftp_name}' for "
                    f"station '{station_name}' from FTP path '{current_path}'"
                )
                
                if not ftp_client.cd(current_path):
                    logger.warning(f"Path {current_path} not found")
                    continue
                
                yield from self._process_path(
                    station_link,
                    current_path,
                    decoder,
                    ftp_client,
                    start_date=start_date,
                    end_date=end_date
                )
        
        except Exception as e:
            logger.error(f"Error fetching FTP data: {e}")
            raise
        finally:
            if ftp_client:
                ftp_client.close()
    
    def _process_path(
            self,
            station_link,
            path: str,
            decoder,
            ftp_client,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Generator that processes a single FTP path and yields records.
        
        Files are processed one at a time, and records are yielded
        as they are decoded.
        """
        logger = self.get_logger()
        
        station = station_link.station
        
        logger.debug(f"Getting list of files in path {path}")
        ftp_files_list = ftp_client.list(path, extra=True)
        pattern = station_link.file_pattern
        
        files_list = [file["name"] for file in ftp_files_list]
        
        matching_files = decoder.get_matching_files(station_link, files_list, start_date, end_date)
        
        if not matching_files:
            logger.debug(
                f"No files found for station {station.name} matching "
                f"pattern {pattern} in path {path}"
            )
            return
        
        logger.debug(
            f"Found {len(matching_files)} matching files for station {station.name} in path {path}"
        )
        
        for file_name in matching_files:
            yield from self._process_file(
                station_link,
                path,
                file_name,
                decoder,
                ftp_client
            )
    
    def _process_file(self, station_link, path, file_name, decoder, ftp_client):
        """
         Generator that processes a single FTP file and yields its records.
         """
        
        logger = self.get_logger()
        
        # Check if file exists in database
        db_data_file = FTPStationDataFile.objects.filter(
            station_link=station_link,
            file_name=file_name
        ).first()
        
        # Skip if already downloaded and skip setting is enabled
        if db_data_file and station_link.skip_already_downloaded_files:
            logger.debug(f"File {file_name} already downloaded, skipping")
            return
        
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
                
                finally:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        
        # Decode and yield records
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
