import fnmatch
import logging
import tempfile

from adl.core.models import ObservationRecord
from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from .ftp import FTPClient
from .models import NetworkFTP, FTPStationDataFile
from .registries import ftp_decoder_registry
from .utils import (
    normalize_path,
    get_dates_to_now,
    get_date_paths
)

logger = logging.getLogger(__name__)


class AdlFtpPlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"
    
    network = None
    decoder = None
    ftp = None
    variable_mappings = None
    
    def get_urls(self):
        return []
    
    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)
    
    def run_process(self, network):
        self.network = network
        return super().run_process(network)
    
    def get_data(self):
        if self.network:
            network_ftp = NetworkFTP.objects.filter(network=self.network).first()
            decoder_name = network_ftp.decoder
            decoder = self.get_decoder(decoder_name)
            
            if not decoder:
                logger.error(f"[ADL_FTP_PLUGIN] Decoder {decoder_name} not found in decoder registry.")
                return
            
            # found decoder
            self.decoder = decoder
            
            variable_mappings = network_ftp.variable_mappings.all()
            
            if not variable_mappings:
                logger.warning(
                    f"[ADL_FTP_PLUGIN] No variable mappings found for network {network_ftp.network.name}. Skipping...")
                return
            
            self.variable_mappings = variable_mappings
            
            if network_ftp:
                logger.info(f"[ADL_FTP_PLUGIN] Getting data from FTP network {network_ftp.network.name}")
                
                # Create FTP client
                self.ftp = FTPClient(host=network_ftp.host, port=network_ftp.port, user=network_ftp.username,
                                     password=network_ftp.password)
                
                station_links = network_ftp.station_links.all()
                
                for station_link in station_links:
                    self.process_station_link(station_link)
                
                # close the connection
                self.ftp.close()
    
    def process_station_link(self, station_link):
        logger.info(f"[ADL_FTP_PLUGIN] Getting data for station {station_link.station.name}")
        timezone_info = station_link.timezone
        
        path = station_link.ftp_path
        
        # Add date info to path if structured by date
        if station_link.dir_structured_by_date and station_link.date_granularity:
            date_granularity = station_link.date_granularity
            
            if station_link.start_date:
                dates = get_dates_to_now(date_granularity, timezone_info, station_link.start_date)
            else:
                dates = get_dates_to_now(date_granularity, timezone_info, dj_timezone.now())
            
            paths = get_date_paths(path, dates, date_granularity)
        else:
            paths = [path]
        
        # Process each path
        for path in paths:
            # check if the path exists
            if not self.ftp.cd(path):
                logger.warning(f"[ADL_FTP_PLUGIN] Path {path} not found")
                continue
            
            self.process_path(station_link, path)
    
    def process_path(self, station_link, path):
        station = station_link.station
        
        logger.info(f"[ADL_FTP_PLUGIN] Getting list of files in path {path}")
        files = self.ftp.list(path, extra=True)
        pattern = station_link.file_pattern
        
        # Filter files by pattern
        matching_files = [file for file in files if fnmatch.fnmatch(file["name"], pattern)]
        
        # If no files found, log and continue
        if not matching_files:
            logger.info(f"[ADL_FTP_PLUGIN] No files found for station {station.name} matching "
                        f"pattern {pattern} in path {path}")
        else:
            logger.info(
                f"[ADL_FTP_PLUGIN] Found {len(matching_files)} matching files for station {station.name}")
        
        # Process each file
        for file in matching_files:
            file_name = file["name"]
            
            # Check if this file was already downloaded
            db_data_file = FTPStationDataFile.objects.filter(station_link=station_link,
                                                             file_name=file_name).first()
            
            if db_data_file and station_link.skip_already_downloaded_files:
                logger.info(f"[ADL_FTP_PLUGIN] File {file_name} already downloaded")
            
            if not db_data_file or not station_link.skip_already_downloaded_files:
                remote_file_path = normalize_path(f"{path}/{file_name}")
                
                with tempfile.NamedTemporaryFile(suffix=file_name) as temp_file:
                    logger.info(f"[ADL_FTP_PLUGIN] Downloading file {file_name}..")
                    self.ftp.get(remote_file_path, temp_file.name)
                    
                    db_data_file = FTPStationDataFile(
                        station_link=station_link,  # Pass the appropriate FTPStationLink instance
                        file_name=file_name,
                    )
                    
                    db_data_file.file.save(file_name, temp_file)
            
            logger.info(f"[ADL_FTP_PLUGIN] Processing file {file_name}")
            
            if db_data_file.processed and station_link.skip_already_processed_files:
                logger.info(f"[ADL_FTP_PLUGIN] File {file_name} already processed. Skipping..")
                continue
            
            self.process_file(db_data_file, station_link, self.variable_mappings)
    
    def process_file(self, db_data_file, station_link, variable_mappings):
        timezone_info = station_link.timezone
        station = station_link.station
        
        data = self.decoder.decode(db_data_file.file.path)
        data_values = data.get("values")
        
        record_count = len(data_values)
        
        file_obs_records = []
        
        for i, record in enumerate(data_values):
            logger.info(f"[ADL_FTP_PLUGIN] Processing record {i + 1}/{record_count}")
            
            timestamp = record.get("TIMESTAMP")
            
            if not timestamp:
                logger.warning(f"[ADL_FTP_PLUGIN] No timestamp found in record {record}")
                continue
            
            utc_obs_date = dj_timezone.make_aware(timestamp, timezone_info)
            
            for variable_mapping in variable_mappings:
                adl_parameter = variable_mapping.adl_parameter
                file_variable_name = variable_mapping.file_variable_name
                file_variable_units = variable_mapping.file_variable_units
                
                value = record.get(file_variable_name)
                
                if value is not None:
                    try:
                        value = adl_parameter.convert_value_units(value, file_variable_units)
                        
                        record_data = {
                            "station": station,
                            "parameter": adl_parameter,
                            "time": utc_obs_date,
                            "value": value,
                            "connection": station_link.network_connection,
                        }
                        
                        param_obs_record = ObservationRecord(**record_data)
                        file_obs_records.append(param_obs_record)
                    except Exception as e:
                        logger.error(f"[ADL_FTP_PLUGIN] Error converting value for parameter "
                                     f"{adl_parameter.parameter}: {e}")
                else:
                    logger.info(
                        f"[ADL_FTP_PLUGIN] No data recorded for parameter {adl_parameter.parameter} ")
        
        if file_obs_records:
            logger.info(
                f"[ADL_FTP_PLUGIN] Saving {len(file_obs_records)} parameter records for station {station.name}")
            ObservationRecord.objects.bulk_create(file_obs_records, ignore_conflicts=True)
            
            # Mark the db data file as processed
            db_data_file.processed = True
            db_data_file.save()
