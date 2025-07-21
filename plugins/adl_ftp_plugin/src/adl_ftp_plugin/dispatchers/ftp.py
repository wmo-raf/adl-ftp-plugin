import csv
import logging
from io import StringIO, BytesIO

from adl.core.utils import get_object_or_none

from adl_ftp_plugin.ftp import FTPClient

logger = logging.getLogger(__name__)


def channel_record_to_ftp_file(record, parameter_mappings, timezone="UTC"):
    from adl.core.models import Station
    station_id = record.get("station_id")
    
    if not station_id:
        logger.error("Station ID not found in data record")
        return None, None
    
    timestamp = record.get("timestamp")
    # convert to set timezone if provided
    timestamp = timestamp.astimezone(timezone)
    
    if not timestamp:
        logger.error("Timestamp not found in data record")
        return None, None
    
    station = get_object_or_none(Station, id=station_id)
    
    if not station:
        logger.error(f"Station with ID {station_id} not found")
        return None, None
    
    channel_params = [pm.channel_parameter for pm in parameter_mappings]
    
    header = [
        "station_id",
        "date",
        "time",
        *channel_params
    ]
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    
    row_data = []
    values = record.get("values", {})
    
    date_info = {
        "date": timestamp.strftime("%Y-%m-%d"),
        "time": timestamp.strftime("%H:%M:%S"),
    }
    
    data = {
        "station_id": station.wigos_id,
        **date_info,
        **values,
    }
    
    for col in header:
        col_data = data.get(col, "")
        row_data.append(col_data)
    
    writer.writerow(row_data)
    csv_content = output.getvalue()
    output.close()
    
    filename = f"WIGOS_{station.wigos_id}_{timestamp.strftime('%Y%m%dT%H%M%S')}.csv"
    
    return csv_content, station.wigos_id, filename,


def upload_to_ftp(channel, data_records):
    from adl.core.models import StationChannelDispatchStatus
    
    ftp_client = FTPClient(
        **channel.connection_details
    )
    
    uploaded_records_count = 0
    parameter_mappings = channel.parameter_mappings.all()
    timezone = channel.timezone
    
    try:
        for record in data_records:
            csv_content, wigos_id, filename = channel_record_to_ftp_file(record, parameter_mappings, timezone)
            
            if not csv_content:
                logger.error("Error converting record to CSV. Skipping...")
            
            # Convert csv_content to bytes for uploading
            csv_bytes = BytesIO(csv_content.encode('utf-8'))
            
            remote_file = f"{channel.directory}/{wigos_id}/{filename}"
            
            ftp_client.put(csv_bytes, remote_file)
            
            logger.debug(f"Uploaded {filename} to {remote_file}")
            
            station_id = record.get("station_id")
            
            logger.debug(f"Updating last sent observation time for station {station_id} and channel {channel.name}")
            
            station_dispatch_status = get_object_or_none(
                StationChannelDispatchStatus,
                channel_id=channel.id,
                station_id=station_id
            )
            
            if station_dispatch_status:
                station_dispatch_status.last_sent_obs_time = record.get("timestamp")
                station_dispatch_status.save()
            else:
                StationChannelDispatchStatus.objects.create(
                    channel_id=channel.id,
                    station_id=station_id,
                    last_sent_obs_time=record.get("timestamp")
                )
            
            uploaded_records_count += 1
    except Exception as e:
        raise e
    finally:
        ftp_client.close()
    
    logger.info(f"Uploaded {uploaded_records_count} records to {channel.name}")
    return uploaded_records_count
