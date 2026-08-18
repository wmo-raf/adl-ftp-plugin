import csv
from io import StringIO, BytesIO
from typing import List


def get_station_metadata_csv(dispatch_channel) -> BytesIO:
    """
    Generate CSV metadata for all allowed stations in a dispatch channel.

    Args:
        dispatch_channel: DispatchChannel instance

    Returns:
        BytesIO object containing CSV data without headers

    CSV format: station_id,station_number,longitude,latitude,name
    """
    output = StringIO()
    writer = csv.writer(output)

    # Get all allowed station links for this dispatch channel
    allowed_station_links = dispatch_channel.stations_allowed_to_send()

    # Extract station metadata for each allowed station
    for station_link in allowed_station_links:
        station = station_link.station

        # Extract coordinates from the PointField (location)
        longitude = station.location.x if station.location else ""
        latitude = station.location.y if station.location else ""

        # Us PK station id as station number
        station_number = station.id

        # Write the row: station_number,station_number,longitude,latitude,name
        writer.writerow([
            station_number,
            station_number,
            longitude,
            latitude,
            station.name
        ])

    # Convert to BytesIO for consistency with other CSV functions
    return BytesIO(output.getvalue().encode("utf-8"))


def get_station_metadata_records(dispatch_channel) -> List[dict]:
    """
    Get station metadata as a list of dictionaries (useful for further processing).

    Args:
        dispatch_channel: DispatchChannel instance

    Returns:
        List of dictionaries with station metadata
    """
    stations_metadata = []

    # Get all allowed station links for this dispatch channel
    allowed_station_links = dispatch_channel.stations_allowed_to_send()

    # Extract station metadata for each allowed station
    for station_link in allowed_station_links:
        station = station_link.station

        # Extract coordinates from the PointField (location)
        longitude = station.location.x if station.location else None
        latitude = station.location.y if station.location else None

        # Use WMO station number if available, otherwise fall back to station_id
        station_number = station.wmo_station_number or station.station_id

        stations_metadata.append({
            'station_id': station.station_id,
            'station_number': station_number,
            'longitude': longitude,
            'latitude': latitude,
            'name': station.name
        })

    return stations_metadata
