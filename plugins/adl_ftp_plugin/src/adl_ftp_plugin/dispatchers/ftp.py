import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from ftplib import error_perm
from io import StringIO, BytesIO
from typing import List, Dict

import pandas as pd
from adl_ftp_plugin.ftp import FTPClient

from adl.core.utils import get_object_or_none

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
    wigos_id: str
    timestamp: pd.Timestamp
    values: dict
    
    def to_row(self, header: List[str], timezone="UTC") -> List[str]:
        dt = self.timestamp.astimezone(timezone)
        base = {
            "station_id": self.station_id,
            "wigos_id": self.wigos_id,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M:%S"),
            **self.values
        }
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
        sid = record.wigos_id
        day = record.timestamp.strftime("%Y-%m-%d")
        grouped.setdefault(sid, {}).setdefault(day, {})[record.timestamp] = record
    return grouped


def create_csv_file(records: List[ObsRecord], header: List[str], timezone) -> BytesIO:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    for record in records:
        writer.writerow(record.to_row(header, timezone))
    return BytesIO(output.getvalue().encode("utf-8"))


# --- FTP Upload Logic ---

def upload_to_ftp(channel, data_records: List[Dict]):
    ftp = FTPClient(**channel.connection_details)
    timezone = channel.timezone
    write_mode = channel.write_mode
    channel_params = [pm.channel_parameter for pm in channel.parameter_mappings.all()]
    csv_header = ["station_id", "wigos_id", "date", "time"] + channel_params
    uploaded = 0
    last_sent_obs_time = None
    
    try:
        if write_mode == "new_file":
            logger.debug(f"[FTP Dispatch] Creating new files for {len(data_records)} records")
            for data in data_records:
                record = ObsRecord(
                    station_id=data.get("station_id"),
                    wigos_id=data.get("wigos_id"),
                    timestamp=make_aware_timestamp(data.get("timestamp"), timezone),
                    values=extract_values(data, channel_params, from_root=False)
                )
                csv_file = create_csv_file([record], csv_header, timezone)
                filename = f"WIGOS_{record.wigos_id}_{record.timestamp.strftime('%Y%m%dT%H%M%S')}.csv"
                remote_path = f"{channel.directory}/{record.wigos_id}/{filename}"
                
                logger.debug(f"[FTP Dispatch] Uploading file to '{remote_path}'")
                
                ftp.put(csv_file, remote_path)
                last_sent_obs_time = record.timestamp
                uploaded += 1
        
        elif write_mode == "append":
            grouped = group_records_by_station_day([
                ObsRecord(
                    station_id=d.get("station_id"),
                    wigos_id=d.get("wigos_id"),
                    timestamp=make_aware_timestamp(d.get("timestamp"), timezone),
                    values=extract_values(d, channel_params, from_root=False))
                for d in data_records
            ])
            
            for wigos_id, days in grouped.items():
                for day, incoming_records in days.items():
                    filename = f"WIGOS_{wigos_id}_{day.replace('-', '')}.csv"
                    remote_path = f"{channel.directory}/{wigos_id}/{filename}"
                    final_records = {}
                    
                    try:
                        logger.debug(f"[FTP Dispatch] Checking for existing file at '{remote_path}'")
                        existing_csv = ftp.get(remote_path)
                        
                        logger.debug(f"[FTP Dispatch] Found existing file at '{remote_path}'")
                        
                        existing_records = csv_to_records(existing_csv, csv_header, channel_params, timezone)
                        existing_timestamps = {r.timestamp for r in existing_records}
                        
                        logger.debug(f"[FTP Dispatch] Checking for new records to append for '{remote_path}'")
                        
                        # check if we have new records to append
                        new_records = []
                        for ts, record in incoming_records.items():
                            if ts not in existing_timestamps:
                                new_records.append(record)
                        
                        if not new_records:
                            logger.debug(f"[FTP Dispatch] No new records to append for '{remote_path}'. Skipping..")
                            continue
                        
                        logger.debug(f"[FTP Dispatch] Found {len(new_records)} new records. Appending...")
                        
                        final_records = {r.timestamp: r for r in existing_records}
                    except error_perm:
                        logger.debug(f"[FTP Dispatch] No existing file for '{remote_path}'. Creating new.")
                    
                    # Append new records to existing ones
                    final_records.update(incoming_records)
                    
                    final_records = list(final_records.values())
                    csv_file = create_csv_file(final_records, csv_header, timezone)
                    ftp.put(csv_file, remote_path)
                    latest_record = max(final_records, key=lambda r: r.timestamp)
                    last_sent_obs_time = latest_record.timestamp
                    uploaded += 1
    
    finally:
        ftp.close()
    
    logger.info(f"[FTP Dispatch] Uploaded {uploaded} file to {channel.name}")
    return uploaded, last_sent_obs_time
