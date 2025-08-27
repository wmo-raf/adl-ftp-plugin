import logging
import tempfile

from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from .ftp import FTPClient
from .models import FTPStationDataFile
from .registries import ftp_decoder_registry
from .utils import (
    normalize_path,
    get_dates_to_now,
    get_date_paths,
)

logger = logging.getLogger(__name__)


class AdlFtpPlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"
    
    network_conn_ftp = None
    decoder = None
    ftp = None
    
    def get_urls(self):
        return []
    
    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)
    
    def get_default_start_date(self, station_link):
        start_date = dj_timezone.localtime(dj_timezone.now(), timezone=station_link.timezone)
        return start_date
    
    def get_station_data(self, station_link, start_date=None, end_date=None):
        """
        This method is called to get the station data for a given station link.
        It will return the records collected for each station.
        
        :param station_link: The station link that is used to collect the data.
        :type station_link: adl_ftp_plugin.models.FTPStationLink
        :param start_date: The start date for the data collection.
        :param end_date: The end date for the data collection.
        :return: A List with records processed for each station.
        :rtype: list
        """
        
        network_conn_ftp = station_link.network_connection
        decoder_name = network_conn_ftp.decoder
        
        ftp_client = None
        
        try:
            ftp_client = FTPClient(
                host=network_conn_ftp.host,
                port=network_conn_ftp.port,
                user=network_conn_ftp.username,
                password=network_conn_ftp.password
            )
            
            # get the decoder from the registry
            decoder = self.get_decoder(decoder_name)
            
            if not decoder:
                logger.error(f"[ADL_FTP_PLUGIN] Decoder {decoder_name} not found in decoder registry.")
                return
            
            net_ftp_name = network_conn_ftp.network.name
            timezone = station_link.timezone
            station_name = station_link.station.name
            
            path = station_link.ftp_path
            
            # Add date info to path if structured by date
            if station_link.dir_structured_by_date and station_link.date_granularity:
                date_granularity = station_link.date_granularity
                month_dir_format = station_link.month_dir_format
                
                dates = get_dates_to_now(date_granularity=date_granularity, timezone=timezone, from_date=start_date)
                
                paths = get_date_paths(path, dates, date_granularity, month_dir_format)
            else:
                paths = [path]
            
            records = []
            # Process each path
            for path in paths:
                logger.debug(f"[ADL_FTP_PLUGIN] Getting FTP data from '{net_ftp_name}' for "
                             f"station '{station_name}' from FTP path '{path}'")
                
                # check if the path exists
                if not ftp_client.cd(path):
                    logger.warning(f"[ADL_FTP_PLUGIN] Path {path} not found")
                    continue
                
                path_records = self.process_path(station_link, path, decoder, ftp_client, start_date=start_date,
                                                 end_date=end_date)
                records.extend(path_records)
            
            return records
        except Exception as e:
            raise e
        finally:
            if ftp_client:
                ftp_client.close()
    
    def process_path(self, station_link, path, decoder, ftp_client, start_date=None, end_date=None):
        station = station_link.station
        
        logger.debug(f"[ADL_FTP_PLUGIN] Getting list of files in path {path}")
        ftp_files_list = ftp_client.list(path, extra=True)
        pattern = station_link.file_pattern
        
        # get the list of files
        files_list = [file["name"] for file in ftp_files_list]
        
        # get the matching files
        matching_files = decoder.get_matching_files(station_link, files_list, start_date, end_date)
        
        # If no files found, log and continue
        if not matching_files:
            logger.debug(f"[ADL_FTP_PLUGIN] No files found for station {station.name} matching "
                         f"pattern {pattern} in path {path}")
        else:
            logger.debug(
                f"[ADL_FTP_PLUGIN] Found {len(matching_files)} matching files for station {station.name} in path {path}")
        
        records = []
        
        # Process each file
        for file_name in matching_files:
            
            # Check if this file was already downloaded
            db_data_file = FTPStationDataFile.objects.filter(station_link=station_link,
                                                             file_name=file_name).first()
            
            if db_data_file and station_link.skip_already_downloaded_files:
                logger.debug(f"[ADL_FTP_PLUGIN] File {file_name} already downloaded")
            
            if not db_data_file or not station_link.skip_already_downloaded_files:
                remote_file_path = normalize_path(f"{path}/{file_name}")
                
                with tempfile.NamedTemporaryFile(suffix=file_name) as temp_file:
                    logger.debug(f"[ADL_FTP_PLUGIN] Downloading file {file_name}..")
                    ftp_client.get(remote_file_path, temp_file.name)
                    
                    db_data_file = FTPStationDataFile(
                        station_link=station_link,  # Pass the appropriate FTPStationLink instance
                        file_name=file_name,
                    )
                    
                    db_data_file.file.save(file_name, temp_file)
            
            data = decoder.decode(db_data_file.file.path)
            file_records = data.get("values")
            records += file_records
        
        return records
