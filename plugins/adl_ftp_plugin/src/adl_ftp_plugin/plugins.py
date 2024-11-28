import fnmatch
import logging
import tempfile

from adl.core.models import ObservationRecord
from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

from .ftp import FTPClient
from .models import NetworkFTP, FTPStationDataFile
from .registries import ftp_decoder_registry
from .utils import normalize_path, add_date_info_to_path

logger = logging.getLogger(__name__)

datetime_pattern = r"_(\d{14})\.*"


class AdlFtpPlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"
    
    network = None
    
    def get_urls(self):
        return []
    
    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)
    
    def get_data(self):
        if self.network:
            network_ftp = NetworkFTP.objects.filter(network=self.network).first()
            decoder_name = network_ftp.decoder
            FTPDecoder = self.get_decoder(decoder_name)
            
            variable_mappings = network_ftp.variable_mappings.all()
            
            if not variable_mappings:
                logger.warning(
                    f"[ADL_FTP_PLUGIN] No variable mappings found for network {network_ftp.network.name}. Skipping...")
                return
            
            if network_ftp:
                logger.info(f"[ADL_FTP_PLUGIN] Getting data from FTP network {network_ftp.network.name}")
                
                # Create FTP client
                ftp = FTPClient(host=network_ftp.host, port=network_ftp.port, user=network_ftp.username,
                                password=network_ftp.password)
                
                station_links = network_ftp.station_links.all()
                
                for station_link in station_links:
                    logger.info(f"[ADL_FTP_PLUGIN] Getting data for station {station_link.station.name}")
                    station = station_link.station
                    timezone_info = station.timezone
                    
                    path = station_link.ftp_path
                    
                    # Add date info to path if structured by date
                    if station_link.dir_structured_by_date and station_link.date_granularity:
                        date_granularity = station_link.date_granularity
                        date_info = {}
                        now = dj_timezone.now()
                        if date_granularity == "year":
                            date_info.update({"year": now.year})
                        elif date_granularity == "month":
                            date_info.update({"year": now.year, "month": now.month})
                        elif date_granularity == "day":
                            date_info.update({"year": now.year, "month": now.month, "day": now.day})
                        
                        path = add_date_info_to_path(path, date_info)
                    
                    files = ftp.list(path, extra=True)
                    pattern = station_link.file_pattern
                    
                    # Filter files by pattern
                    matching_files = [file for file in files if fnmatch.fnmatch(file["name"], pattern)]
                    
                    # If no files found, log and continue
                    if not matching_files:
                        logger.info(f"[ADL_FTP_PLUGIN] No files found for station {station.name} matching "
                                    f"pattern {pattern}")
                    else:
                        logger.info(f"[ADL_FTP_PLUGIN] Found {len(matching_files)} files for station {station.name}")
                    
                    # Process each file
                    for file in matching_files:
                        file_name = file["name"]
                        
                        # Check if this file was already downloaded
                        data_file = FTPStationDataFile.objects.filter(station_link=station_link,
                                                                      file_name=file_name).first()
                        
                        if data_file and data_file.processed and station_link.skip_already_processed_files:
                            logger.info(
                                f"[ADL_FTP_PLUGIN] File {file_name} already downloaded and processed. Skipping...")
                            continue
                        
                        if not data_file:
                            remote_file_path = normalize_path(f"{path}/{file_name}")
                            
                            logger.info(f"[ADL_FTP_PLUGIN] Processing file {file_name}")
                            
                            with tempfile.NamedTemporaryFile(suffix=file_name) as temp_file:
                                logger.info(f"[ADL_FTP_PLUGIN] Downloading file {file_name}")
                                ftp.get(remote_file_path, temp_file.name)
                                
                                data_file = FTPStationDataFile(
                                    station_link=station_link,  # Pass the appropriate FTPStationLink instance
                                    file_name=file_name,
                                )
                                
                                data_file.file.save(file_name, temp_file)
                        
                        data = FTPDecoder.decode(data_file.file.path)
                        data_values = data.get("values")
                        
                        record_count = len(data_values)
                        
                        obs_records = []
                        
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
                                        }
                                        
                                        obs_record = ObservationRecord(**record_data)
                                        obs_records.append(obs_record)
                                    except Exception as e:
                                        logger.error(f"[ADL_FTP_PLUGIN] Error converting value for parameter "
                                                     f"{adl_parameter.parameter}: {e}")
                                else:
                                    logger.info(
                                        f"[ADL_FTP_PLUGIN] No data recorded for parameter {adl_parameter.parameter} ")
                        
                        if obs_records:
                            logger.info(
                                f"[ADL_FTP_PLUGIN] Saving {len(obs_records)} records for station {station.name}")
                            ObservationRecord.objects.bulk_create(obs_records, ignore_conflicts=True)
                            
                            # Mark the data file as processed
                            data_file.processed = True
                            data_file.save()
                
                # close the connection
                ftp.close()
    
    def run_process(self, network):
        self.network = network
        return super().run_process(network)
