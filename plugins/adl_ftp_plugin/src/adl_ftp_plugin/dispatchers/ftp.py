import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from io import StringIO, BytesIO
from typing import List, Dict, Tuple, Optional

import pandas as pd

# Import both FTP and SFTP error types for compatibility
from adl_ftp_plugin.ftp import FTPError
from adl_ftp_plugin.ftp.sftp import SFTPError

logger = logging.getLogger(__name__)


class FTPFileError(Exception):
    pass


def validate_csv_header(columns, expected):
    if not all(col in columns for col in expected):
        raise FTPFileError("CSV file does not match the expected header format.")


def make_aware_timestamp(ts, timezone):
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize(timezone)
        else:
            ts = ts.tz_convert(timezone)
        return ts.to_pydatetime()
    elif isinstance(ts, datetime):
        if ts.tzinfo is None:
            return timezone.localize(ts)
        return ts.astimezone(timezone)
    else:
        raise TypeError("Unsupported timestamp type")


def build_timestamp(date_series, time_series):
    try:
        dt_series = pd.to_datetime(date_series + ' ' + time_series, errors="coerce")
        if dt_series.isnull().any():
            raise ValueError("Invalid or missing datetime")
        return dt_series
    except Exception as e:
        raise FTPFileError(f"Error processing timestamp: {e}")


def extract_values(record, keys, from_root=True):
    source = record if from_root else record.get("values", {})
    return {key: source.get(key) for key in keys}


@dataclass
class ObsRecord:
    station_id: str
    wigos_id: Optional[str]
    timestamp: pd.Timestamp
    values: dict
    
    def to_row(self, header: List[str], timezone="UTC") -> List[str]:
        dt = self.timestamp.astimezone(timezone)
        base = {
            "station_id": self.station_id,
            **self.values
        }
        
        # Only include wigos_id if it's in the header
        if "wigos_id" in header:
            base["wigos_id"] = self.wigos_id or ""
        
        # Handle timestamp format based on header columns
        if "timestamp" in header:
            # Single timestamp column in UTC ISO format
            base["timestamp"] = dt.strftime("%Y%m%dT%H%M%S")
        else:
            # Separate date and time columns
            base["date"] = dt.strftime("%Y-%m-%d")
            base["time"] = dt.strftime("%H:%M:%S")
        
        return [base.get(col, "") for col in header]


def csv_to_records(csv_content, csv_header, value_columns, timezone="UTC") -> List[ObsRecord]:
    df = pd.read_csv(BytesIO(csv_content))
    validate_csv_header(df.columns, csv_header)
    df["timestamp"] = build_timestamp(df["date"].astype(str), df["time"].astype(str))
    df = df.sort_values(by=["timestamp"])
    
    return [
        ObsRecord(
            station_id=row.get("station_id"),
            wigos_id=row.get("wigos_id"),
            timestamp=make_aware_timestamp(row.get("timestamp"), timezone),
            values=extract_values(row, value_columns)
        ) for row in df.to_dict(orient="records")
    ]


def group_records_by_station_day(records: List[ObsRecord]):
    grouped = {}
    for record in records:
        # Use wigos_id if available, otherwise fall back to station_id
        sid = record.wigos_id or record.station_id
        day = record.timestamp.strftime("%Y-%m-%d")
        grouped.setdefault(sid, {}).setdefault(day, {})[record.timestamp] = record
    return grouped


def create_csv_file(records: List[ObsRecord], header: List[str], timezone, include_header: bool = True) -> BytesIO:
    output = StringIO()
    writer = csv.writer(output)
    
    # Only write header if requested
    if include_header:
        writer.writerow(header)
    
    for record in records:
        writer.writerow(record.to_row(header, timezone))
    return BytesIO(output.getvalue().encode("utf-8"))


# --- Simplified Upload Logic ---

def upload_csv_to_remote(client, csv_file: BytesIO, remote_path: str) -> None:
    """
    Upload a single CSV file to remote server (FTP or SFTP).
    
    Args:
        client: Connected FTP or SFTP client
        csv_file: CSV file as BytesIO object
        remote_path: Full remote path where file should be uploaded
    """
    logger.debug(f"[Remote Upload] Uploading file to '{remote_path}'")
    client.put(csv_file, remote_path)


def has_valid_data(values: dict) -> bool:
    """
    Check if the record has at least one non-empty, non-None channel parameter value.
    
    Args:
        values: Dictionary of channel parameter values
        
    Returns:
        True if at least one value is not None, not empty string, and not just whitespace
    """
    return any(
        value is not None and
        str(value).strip() != ""
        for value in values.values()
    )


def prepare_csv_files_new_mode(
        data_records: List[Dict],
        channel,
        create_station_dir: bool = True,
        include_wigos_id: bool = True,
        use_single_timestamp: bool = False,
        include_header: bool = True
) -> List[Tuple[BytesIO, str]]:
    """
    Prepare CSV files for 'new_file' write mode.
    Each record becomes a separate CSV file.
    Only creates files for records that have at least one valid channel parameter value.
    
    Args:
        include_wigos_id: Whether to include wigos_id column in CSV header
        use_single_timestamp: If True, use single 'timestamp' column in UTC ISO format (20110311T131920).
                             If False, use separate 'date' and 'time' columns.
        include_header: Whether to include the header row in CSV files
    
    Returns:
        List of (csv_file, remote_path) tuples
    """
    timezone = channel.timezone
    channel_params = channel.get_parameter_mapping_values()
    
    # Build header based on flags
    csv_header = ["station_id"]
    if include_wigos_id:
        csv_header.append("wigos_id")
    
    # Add timestamp columns
    if use_single_timestamp:
        csv_header.append("timestamp")
    else:
        csv_header.extend(["date", "time"])
    
    csv_header.extend(channel_params)
    
    csv_files = []
    skipped_records = 0
    
    for data in data_records:
        values = extract_values(data, channel_params, from_root=False)
        
        # Skip records that don't have at least one valid channel parameter value
        if not has_valid_data(values):
            skipped_records += 1
            logger.debug(
                f"[Remote Prepare] Skipping record for station {data.get('station_id')} - no valid channel parameter values")
            continue
        
        record = ObsRecord(
            station_id=data.get("station_id"),
            wigos_id=data.get("wigos_id"),  # Can be None
            timestamp=make_aware_timestamp(data.get("timestamp"), timezone),
            values=values
        )
        
        csv_file = create_csv_file([record], csv_header, timezone, include_header)
        
        # Use wigos_id if available, otherwise use station_id for filename and directory
        id_for_path = record.wigos_id or record.station_id
        filename = f"WIGOS_{id_for_path}_{record.timestamp.strftime('%Y%m%dT%H%M%S')}.csv"
        
        if create_station_dir:
            remote_path = f"{channel.directory}/{id_for_path}/{filename}"
        else:
            remote_path = f"{channel.directory}/{filename}"
        
        csv_files.append((csv_file, remote_path))
    
    if skipped_records > 0:
        logger.info(f"[Remote Prepare] Skipped {skipped_records} records with no valid channel parameter values")
    
    return csv_files


def prepare_csv_files_append_mode(
        data_records: List[Dict],
        channel,
        client,
        create_station_dir: bool = True,
        include_wigos_id: bool = True,
        use_single_timestamp: bool = False,
        include_header: bool = True
) -> List[Tuple[BytesIO, str]]:
    """
    Prepare CSV files for 'append' write mode.
    Groups records by station and day, merging with existing files if they exist.
    Only processes records that have at least one valid channel parameter value.
    
    Args:
        include_wigos_id: Whether to include wigos_id column in CSV header
        use_single_timestamp: If True, use single 'timestamp' column in UTC ISO format (20110311T131920).
                             If False, use separate 'date' and 'time' columns.
        include_header: Whether to include the header row in CSV files
    
    Returns:
        List of (csv_file, remote_path) tuples
    """
    timezone = channel.timezone
    channel_params = channel.get_parameter_mapping_values()
    
    # Build header based on flags
    csv_header = ["station_id"]
    if include_wigos_id:
        csv_header.append("wigos_id")
    
    # Add timestamp columns
    if use_single_timestamp:
        csv_header.append("timestamp")
    else:
        csv_header.extend(["date", "time"])
    
    csv_header.extend(channel_params)
    
    # Filter and convert records, keeping only those with valid data
    valid_records = []
    skipped_records = 0
    
    for d in data_records:
        values = extract_values(d, channel_params, from_root=False)
        
        # Skip records that don't have at least one valid channel parameter value
        if not has_valid_data(values):
            skipped_records += 1
            logger.debug(
                f"[Remote Prepare] Skipping record for station {d.get('station_id')} - no valid channel parameter values")
            continue
        
        valid_records.append(ObsRecord(
            station_id=d.get("station_id"),
            wigos_id=d.get("wigos_id"),  # Can be None
            timestamp=make_aware_timestamp(d.get("timestamp"), timezone),
            values=values
        ))
    
    if skipped_records > 0:
        logger.info(f"[Remote Prepare] Skipped {skipped_records} records with no valid channel parameter values")
    
    # If no valid records remain, return empty list
    if not valid_records:
        logger.info("[Remote Prepare] No valid records to process after filtering")
        return []
    
    # Group records by station and day
    grouped = group_records_by_station_day(valid_records)
    
    csv_files = []
    
    for station_id, days in grouped.items():
        for day, incoming_records in days.items():
            # station_id here is actually the id_for_path (wigos_id or station_id fallback)
            filename = f"WIGOS_{station_id}_{day.replace('-', '')}.csv"
            if create_station_dir:
                remote_path = f"{channel.directory}/{station_id}/{filename}"
            else:
                remote_path = f"{channel.directory}/{filename}"
            
            final_records = {}
            
            try:
                logger.debug(f"[Remote Prepare] Checking for existing file at '{remote_path}'")
                existing_csv = client.get(remote_path)
                
                logger.debug(f"[Remote Prepare] Found existing file at '{remote_path}'")
                
                existing_records = csv_to_records(existing_csv, csv_header, channel_params, timezone)
                existing_timestamps = {r.timestamp for r in existing_records}
                
                logger.debug(f"[Remote Prepare] Checking for new records to append for '{remote_path}'")
                
                # Check if we have new records to append
                new_records = []
                for ts, record in incoming_records.items():
                    if ts not in existing_timestamps:
                        new_records.append(record)
                
                if not new_records:
                    logger.debug(f"[Remote Prepare] No new records to append for '{remote_path}'. Skipping..")
                    continue
                
                logger.debug(f"[Remote Prepare] Found {len(new_records)} new records. Appending...")
                
                final_records = {r.timestamp: r for r in existing_records}
            except (FTPError, SFTPError, Exception) as e:
                # Handle both FTP and SFTP errors, plus any other file-not-found scenarios
                logger.debug(
                    f"[Remote Prepare] No existing file for '{remote_path}' or error accessing it: {e}. Creating new.")
            
            # Append new records to existing ones
            final_records.update(incoming_records)
            
            final_records_list = list(final_records.values())
            csv_file = create_csv_file(final_records_list, csv_header, timezone, include_header)
            csv_files.append((csv_file, remote_path))
    
    return csv_files


def dispatch_to_ftp(channel, data_records: List[Dict], create_station_dir=True, include_wigos_id=True,
                    use_single_timestamp=False, include_header=True):
    """
    Main dispatch function that coordinates CSV preparation and remote upload.
    Works with both FTP and SFTP connections automatically based on channel configuration.
    
    Args:
        channel: Channel configuration object (supports both FTP and SFTP)
        data_records: List of data records to process
        create_station_dir: Whether to create station directories
        include_wigos_id: Whether to include wigos_id column in CSV files
        use_single_timestamp: If True, use single 'timestamp' column in UTC ISO format (20110311T131920).
                             If False, use separate 'date' and 'time' columns.
        include_header: Whether to include the header row in CSV files
    """
    # Use the flexible client method that returns FTP or SFTP client based on configuration
    client = channel.get_client()
    write_mode = channel.write_mode
    uploaded = 0
    last_sent_obs_time = None
    
    # Log the connection type for debugging
    connection_type = getattr(channel, 'connection_type', 'ftp')
    logger.debug(f"[Remote Dispatch] Using {connection_type.upper()} connection for {channel.name}")
    
    try:
        if write_mode == "new_file":
            logger.debug(f"[Remote Dispatch] Creating new files for {len(data_records)} records")
            csv_files = prepare_csv_files_new_mode(data_records, channel, create_station_dir, include_wigos_id,
                                                   use_single_timestamp, include_header)
            
            for csv_file, remote_path in csv_files:
                upload_csv_to_remote(client, csv_file, remote_path)
                uploaded += 1
            
            # Get the last timestamp from the original records
            if data_records:
                timestamps = [make_aware_timestamp(d.get("timestamp"), channel.timezone) for d in data_records]
                last_sent_obs_time = max(timestamps)
        
        elif write_mode == "append":
            logger.debug(f"[Remote Dispatch] Appending records to existing files")
            csv_files = prepare_csv_files_append_mode(data_records, channel, client, create_station_dir,
                                                      include_wigos_id,
                                                      use_single_timestamp, include_header)
            
            for csv_file, remote_path in csv_files:
                upload_csv_to_remote(client, csv_file, remote_path)
                uploaded += 1
            
            # Get the last timestamp from all processed records
            if data_records:
                timestamps = [make_aware_timestamp(d.get("timestamp"), channel.timezone) for d in data_records]
                last_sent_obs_time = max(timestamps)
    
    finally:
        client.close()
    
    logger.info(f"[Remote Dispatch] Uploaded {uploaded} files to {channel.name} via {connection_type.upper()}")
    return uploaded, last_sent_obs_time
