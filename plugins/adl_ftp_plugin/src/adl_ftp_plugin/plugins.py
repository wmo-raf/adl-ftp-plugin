import csv
import fnmatch
import logging
import re
import tempfile
from datetime import datetime
from io import StringIO

from django.core.files.base import ContentFile
from django.utils import timezone as dj_timezone
from wis2box_adl.core.constants import WIS2BOX_CSV_HEADER
from wis2box_adl.core.models import DataIngestionRecord
from wis2box_adl.core.registries import Plugin

from .ftp import FTPClient
from .models import NetworkFTP
from .registries import ftp_decoder_registry
from .utils import normalize_path

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
        ingestion_record_ids = []
        
        if self.network:
            network_ftp = NetworkFTP.objects.filter(network=self.network).first()
            decoder_name = network_ftp.decoder
            FTPDecoder = self.get_decoder(decoder_name)
            extract_date_from_filename = network_ftp.extract_date_from_filename
            
            variable_mappings = network_ftp.variable_mappings.all()
            
            if not variable_mappings:
                logger.warning(
                    f"[ADL_FTP_PLUGIN] No variable mappings found for network {network_ftp.network.name}. Skipping...")
                return
            
            if network_ftp:
                logger.info(f"[ADL_FTP_PLUGIN] Getting data from FTP network {network_ftp.network.name}")
                
                ftp = FTPClient(host=network_ftp.host, port=network_ftp.port, user=network_ftp.username,
                                password=network_ftp.password)
                
                station_links = network_ftp.stations.all()
                
                for station_link in station_links:
                    logger.info(f"[ADL_FTP_PLUGIN] Getting data for station {station_link.station.name}")
                    station = station_link.station
                    timezone = station.timezone
                    
                    last_ingested_record = DataIngestionRecord.objects.filter(station=station).order_by("-time").first()
                    
                    path = station_link.ftp_path
                    files = ftp.list(path, extra=True)
                    pattern = station_link.file_pattern
                    
                    matching_files = [file for file in files if fnmatch.fnmatch(file["name"], pattern)]
                    
                    if not matching_files:
                        logger.info(f"[ADL_FTP_PLUGIN] No files found for station {station.name} matching "
                                    f"pattern {pattern}")
                    else:
                        logger.info(f"[ADL_FTP_PLUGIN] Found {len(matching_files)} files for station {station.name}")
                    
                    for file in matching_files:
                        file_name = file["name"]
                        remote_file_path = normalize_path(f"{path}/{file_name}")
                        
                        if extract_date_from_filename and last_ingested_record:
                            obs_date = None
                            
                            match = re.search(datetime_pattern, file_name)
                            if match:
                                date_str = match.group(1)
                                obs_date = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                                obs_date = dj_timezone.make_aware(obs_date, timezone)
                            else:
                                logger.warning(f"[ADL_FTP_PLUGIN] Could not extract date from filename {file_name}")
                            
                            if obs_date and last_ingested_record.time >= obs_date:
                                logger.info(f"[ADL_FTP_PLUGIN] File {file_name} already ingested")
                        
                        logger.info(f"[ADL_FTP_PLUGIN] Processing file {file_name}")
                        
                        with tempfile.NamedTemporaryFile(suffix=file_name) as temp_file:
                            logger.info(f"[ADL_FTP_PLUGIN] Downloading file {file_name}")
                            ftp.get(remote_file_path, temp_file.name)
                            
                            data = FTPDecoder.decode(temp_file.name)
                            data_values = data.get("values")
                            
                            record_count = len(data_values)
                            
                            for i, record in enumerate(data_values):
                                
                                print(record, "RECORD")
                                
                                logger.info(f"[ADL_FTP_PLUGIN] Processing record {i + 1}/{record_count}")
                                
                                timestamp = record.get("TIMESTAMP")
                                
                                if not timestamp:
                                    logger.warning(f"[ADL_FTP_PLUGIN] No timestamp found in record {record}")
                                    continue
                                
                                utc_obs_date = dj_timezone.make_aware(timestamp, timezone)
                                
                                ingestion_record = DataIngestionRecord.objects.filter(station=station,
                                                                                      time=utc_obs_date).first()
                                
                                if ingestion_record:
                                    logger.info(f"[ADL_FTP_PLUGIN] Data already ingested for station {station.name} "
                                                f"at {utc_obs_date}")
                                    continue
                                
                                ingestion_data = {}
                                
                                for variable_mapping in variable_mappings:
                                    adl_parameter = variable_mapping.adl_parameter
                                    file_variable_name = variable_mapping.file_variable_name
                                    file_variable_units = variable_mapping.file_variable_units
                                    
                                    value = record.get(file_variable_name)
                                    
                                    if value is not None:
                                        try:
                                            value = adl_parameter.convert_value_units(value, file_variable_units)
                                            ingestion_data.update({adl_parameter.parameter: value})
                                        except Exception as e:
                                            logger.error(f"[ADL_FTP_PLUGIN] Error converting value for parameter "
                                                         f"{adl_parameter.parameter}: {e}")
                                    else:
                                        logger.info(
                                            f"[ADL_FTP_PLUGIN] No data recorded for parameter {adl_parameter.parameter} ")
                                    
                                    date_info = {
                                        "year": utc_obs_date.year,
                                        "month": utc_obs_date.month,
                                        "day": utc_obs_date.day,
                                        "hour": utc_obs_date.hour,
                                        "minute": utc_obs_date.minute,
                                    }
                                    
                                    ingestion_data.update(**date_info, **station.wis2box_csv_metadata)
                                    
                                    filename = f"WIGOS_{station.wigos_id}_{utc_obs_date.strftime('%Y%m%dT%H%M%S')}.csv"
                                    
                                    output = StringIO()
                                    writer = csv.writer(output)
                                    writer.writerow(WIS2BOX_CSV_HEADER)
                                    
                                    row_data = []
                                    for col in WIS2BOX_CSV_HEADER:
                                        col_data = ingestion_data.get(col, "")
                                        row_data.append(col_data)
                                    
                                    writer.writerow(row_data)
                                    csv_content = output.getvalue()
                                    output.close()
                                    
                                    file = ContentFile(csv_content, filename)
                                    
                                    # ingestion_record = DataIngestionRecord.objects.create(
                                    #     station=station,
                                    #     time=utc_obs_date,
                                    #     file=file)
                                    #
                                    # ingestion_record_ids.append(ingestion_record.pk)
        
        return ingestion_record_ids
    
    def run_process(self, network):
        self.network = network
        return super().run_process(network)
