import fnmatch
import logging
import tempfile

import pandas as pd
from adl.core.models import ObservationRecord
from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from .ftp import FTPClient
from .models import FTPStationDataFile
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
    
    network_conn_ftp = None
    decoder = None
    ftp = None
    variable_mappings = None
    
    def get_urls(self):
        return []
    
    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)
    
    def run_process(self, network_connection):
        self.network_conn_ftp = network_connection
        return super().run_process(network_connection)
    
    def get_data(self):
        if not self.network_conn_ftp:
            logger.error("[ADL_FTP_PLUGIN] Network Connection not set. Skipping...")
            return
        
        network_conn_name = self.network_conn_ftp.name
        decoder_name = self.network_conn_ftp.decoder
        decoder = self.get_decoder(decoder_name)
        
        if not decoder:
            logger.error(f"[ADL_FTP_PLUGIN] Decoder {decoder_name} not found in decoder registry.")
            return
        
        # found decoder
        self.decoder = decoder
        
        variable_mappings = self.network_conn_ftp.variable_mappings.all()
        
        if not variable_mappings:
            logger.warning(
                f"[ADL_FTP_PLUGIN] No variable mappings found for network {network_conn_name}. Skipping...")
            return
        
        self.variable_mappings = variable_mappings
        
        logger.info(
            f"[ADL_FTP_PLUGIN] Starting Processing FTP data for network {network_conn_name}"
        )
        
        station_links = self.network_conn_ftp.station_links.all()
        
        logger.debug(
            f"[ADL_FTP_PLUGIN] Found {len(station_links)} station links for network connection {network_conn_name}"
        )
        
        stations_records_count = {}
        
        for station_link in station_links:
            station_name = station_link.station.name
            
            if not station_link.enabled:
                logger.debug(f"[ADL_FTP_PLUGIN] Station link {station_name} is not enabled. Skipping..")
                continue
            
            logger.debug(f"[ADL_FTP_PLUGIN] Processing station link {station_name}")
            
            # Create FTP client
            self.ftp = FTPClient(
                host=self.network_conn_ftp.host,
                port=self.network_conn_ftp.port,
                user=self.network_conn_ftp.username,
                password=self.network_conn_ftp.password
            )
            
            station_link_records_count = self.process_station_link(station_link)
            
            stations_records_count[station_link.station.id] = station_link_records_count
            
            # close the connection
            self.ftp.close()
        
        logger.info(f"[ADL_FTP_PLUGIN] Finished Processing FTP data for network connection {network_conn_name}")
        
        return stations_records_count
    
    def process_station_link(self, station_link):
        net_ftp_name = self.network_conn_ftp.network.name
        timezone_info = station_link.timezone
        station_name = station_link.station.name
        
        path = station_link.ftp_path
        
        # Add date info to path if structured by date
        if station_link.dir_structured_by_date and station_link.date_granularity:
            date_granularity = station_link.date_granularity
            
            start_date = dj_timezone.localtime()
            
            # use the start date if set
            if station_link.start_date:
                start_date = station_link.start_date
            
            dates = get_dates_to_now(date_granularity=date_granularity, timezone=timezone_info, from_date=start_date)
            
            paths = get_date_paths(path, dates, date_granularity)
        else:
            paths = [path]
        
        records_count = 0
        # Process each path
        for path in paths:
            logger.debug(
                f"[ADL_FTP_PLUGIN] Getting FTP data from '{net_ftp_name}' for station '{station_name}' from FTP path '{path}'"
            )
            
            # check if the path exists
            if not self.ftp.cd(path):
                logger.warning(f"[ADL_FTP_PLUGIN] Path {path} not found")
                continue
            
            path_records_count = self.process_path(station_link, path)
            records_count += path_records_count
        
        return records_count
    
    def process_path(self, station_link, path):
        station = station_link.station
        
        logger.debug(f"[ADL_FTP_PLUGIN] Getting list of files in path {path}")
        files = self.ftp.list(path, extra=True)
        pattern = station_link.file_pattern
        
        matching_files = self.decoder.get_matching_files(station_link, files)
        
        # If no files found, log and continue
        if not matching_files:
            logger.debug(f"[ADL_FTP_PLUGIN] No files found for station {station.name} matching "
                         f"pattern {pattern} in path {path}")
        else:
            logger.debug(
                f"[ADL_FTP_PLUGIN] Found {len(matching_files)} matching files for station {station.name} in path {path}")
        
        records_count = 0
        # Process each file
        for file in matching_files:
            file_name = file["name"]
            
            # Check if this file was already downloaded
            db_data_file = FTPStationDataFile.objects.filter(station_link=station_link,
                                                             file_name=file_name).first()
            
            if db_data_file and station_link.skip_already_downloaded_files:
                logger.debug(f"[ADL_FTP_PLUGIN] File {file_name} already downloaded")
            
            if not db_data_file or not station_link.skip_already_downloaded_files:
                remote_file_path = normalize_path(f"{path}/{file_name}")
                
                with tempfile.NamedTemporaryFile(suffix=file_name) as temp_file:
                    logger.debug(f"[ADL_FTP_PLUGIN] Downloading file {file_name}..")
                    self.ftp.get(remote_file_path, temp_file.name)
                    
                    db_data_file = FTPStationDataFile(
                        station_link=station_link,  # Pass the appropriate FTPStationLink instance
                        file_name=file_name,
                    )
                    
                    db_data_file.file.save(file_name, temp_file)
            
            logger.debug(f"[ADL_FTP_PLUGIN] Processing file {file_name}")
            
            if db_data_file.processed and station_link.skip_already_processed_files:
                logger.debug(f"[ADL_FTP_PLUGIN] File {file_name} already processed. Skipping..")
                continue
            
            file_records_count = self.process_file(db_data_file, station_link, self.variable_mappings)
            
            records_count += file_records_count
        
        return records_count
    
    def process_file(self, db_data_file, station_link, variable_mappings):
        timezone_info = station_link.timezone
        station = station_link.station
        
        data = self.decoder.decode(db_data_file.file.path)
        data_values = data.get("values")
        
        record_count = len(data_values)
        
        file_obs_records = []
        
        for i, record in enumerate(data_values):
            logger.debug(f"[ADL_FTP_PLUGIN] Processing record {i + 1}/{record_count}")
            
            timestamp = record.get("TIMESTAMP")
            
            if not timestamp:
                logger.debug(f"[ADL_FTP_PLUGIN] No timestamp found in record {record}")
                continue
            
            utc_obs_date = dj_timezone.make_aware(timestamp, timezone_info)
            
            for variable_mapping in variable_mappings:
                adl_parameter = variable_mapping.adl_parameter
                file_variable_name = variable_mapping.file_variable_name
                file_variable_unit = variable_mapping.file_variable_unit
                
                value = record.get(file_variable_name)
                
                # is None or nan
                if value is None or pd.isna(value):
                    logger.debug(f"[ADL_FTP_PLUGIN] No data record found for parameter {adl_parameter.name}")
                    continue
                
                if adl_parameter.unit != file_variable_unit:
                    value = adl_parameter.convert_value_from_units(value, file_variable_unit)
                
                record_data = {
                    "station": station,
                    "parameter": adl_parameter,
                    "time": utc_obs_date,
                    "value": value,
                    "connection": station_link.network_connection,
                    "is_daily": station_link.network_connection.is_daily_data,
                }
                
                param_obs_record = ObservationRecord(**record_data)
                file_obs_records.append(param_obs_record)
        
        records_count = len(file_obs_records)
        
        if file_obs_records:
            logger.debug(f"[ADL_FTP_PLUGIN] Saving {records_count} parameter records for station {station.name}")
            ObservationRecord.objects.bulk_create(
                file_obs_records,
                update_conflicts=True,
                update_fields=["value"],
                unique_fields=["station", "parameter", "time", "connection"]
            )
            
            # Mark the db data file as processed
            db_data_file.processed = True
            db_data_file.save()
        
        return records_count
