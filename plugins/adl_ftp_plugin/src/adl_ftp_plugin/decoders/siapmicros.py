from csv import reader as csv_reader
from datetime import datetime

from ..registries import FTPDecoder

VALUE_TYPES = {
    "A": "Instantaneous",
    "B": "Average",
    "C": "Minimum",
    "D": "Maximum",
}

PARAMETER_LOOKUP = {
    "1": {
        "name": "Temperature",
        "unit": "°C",
    },
    "2": {
        "name": "Relative Humidity",
        "unit": "%",
    },
    "3": {
        "name": "Barometric Pressure",
        "unit": "hPa",
    },
    "4": {
        "name": "Wind Speed (Anemometer #1) 2 min",
        "unit": "m/s",
    },
    "5": {
        "name": "Wind Speed (Anemometer #1) 10 min",
        "unit": "m/s",
    },
    "6": {
        "name": "Wind Direction (Anemometer #1) 2 min",
        "unit": "°",
    },
    "7": {
        "name": "Wind Direction (Anemometer #1) 10 min",
        "unit": "°",
    },
    "8": {
        "name": "Wind Speed (Anemometer #2) 2 min",
        "unit": "m/s",
    },
    "9": {
        "name": "Wind Speed (Anemometer #2) 10 min",
        "unit": "m/s",
    },
    "32": {
        "name": "Rainfall",
        "unit": "mm",
    },
    "12": {
        "name": "Leaf Wetness (Minutes)",
        "unit": "minutes",
    },
    "13": {
        "name": "Leaf Wetness (MilliVolts)",
        "unit": "mV",
    },
    "14": {
        "name": "Solar Radiation",
        "unit": "W/m2",
    },
    "15": {
        "name": "Solar Radiation",
        "unit": "Mj/m2",
    },
    "16": {
        "name": "Solar Radiation",
        "unit": "Tilt",
    },
    "17": {
        "name": "Sunshine Duration 10 min",
        "unit": "minutes",
    },
    "18": {
        "name": "Sunshine Duration Daily Total",
        "unit": "minutes",
    },
    "19": {
        "name": "Soil Moisture 10 cm",
        "unit": "%",
    },
    "20": {
        "name": "Soil Moisture 20 cm",
        "unit": "%",
    },
    "21": {
        "name": "Soil Moisture 40 cm",
        "unit": "%",
    },
    "22": {
        "name": "Soil Moisture 60 cm",
        "unit": "%",
    },
    "23": {
        "name": "Soil Moisture 80 cm",
        "unit": "%",
    },
    "24": {
        "name": "Soil Moisture 100 cm",
        "unit": "%",
    },
    "25": {
        "name": "Soil Temperature 10 cm",
        "unit": "°C",
    },
    "26": {
        "name": "Soil Temperature 20 cm",
        "unit": "°C",
    },
    "27": {
        "name": "Soil Temperature 40 cm",
        "unit": "°C",
    },
    "28": {
        "name": "Soil Temperature 60 cm",
        "unit": "°C",
    },
    "29": {
        "name": "Soil Temperature 80 cm",
        "unit": "°C",
    },
    "30": {
        "name": "Soil Temperature 100 cm",
        "unit": "°C",
    },
    "31": {
        "name": "Evapotranspiration (Daily Total)",
        "unit": "mm",
    },
    "66": {
        "name": "Battery Voltage",
        "unit": "v",
    },
    "63": {
        "name": "Charge Current",
        "unit": "mA",
    },
    "64": {
        "name": "Discharge Current",
        "unit": "mA",
    },
    "67": {
        "name": "Voltage Solar Panel",
        "unit": "°C",
    },
}


class SiapMicrosDecoder(FTPDecoder):
    """
    This class represents a decoder for the TOA5 data format.
    """
    
    type = "siapmicros"
    compat_type = "siapmicros"
    display_name = "SIAP+Micros"
    
    def decode(self, file_path):
        data = {
            "values": [],
        }
        with open(file_path, "r", encoding="UTF-8") as f_in:
            reader = csv_reader(line.replace('\0', '') for line in f_in)
            
            for line in reader:
                check_field = line[len(line) - 1]
                if not check_field.startswith("#"):
                    raise ValueError("The last field of the line should start with a '#' character.")
                
                # check count
                count = int(check_field[1:])
                if not len(line) == count:
                    raise ValueError(
                        "The count does not match the number of fields. Expected: {0}, Actual: {1}".format(count,
                                                                                                           len(line)))
                
                # station id
                station_id = line[0]
                
                # get dates
                hh, mm, ss = line[2].split(".")
                day = line[3]
                month = line[4]
                year = line[5]
                
                obs_date = f"{year}-{month}-{day} {hh}:{mm}:{ss}"
                obs_date = datetime.strptime(obs_date, "%Y-%m-%d %H:%M:%S")
                
                # extract blocks of data
                num_of_blocks = int(line[7].split("M")[1])
                blocks_data = line[8:8 + num_of_blocks * 3]
                
                # split every 3 elements
                blocks_units_data = [blocks_data[i:i + 3] for i in range(0, len(blocks_data), 3)]
                
                # check if the number of blocks is correct
                if not len(blocks_units_data) == num_of_blocks:
                    raise ValueError(
                        f"The number of blocks data found :{len(blocks_units_data)} is not equal to the number of expected blocks: {num_of_blocks}")
                
                params_data = {
                    "station_id": station_id,
                    "TIMESTAMP": obs_date,
                }
                
                for param_data in blocks_units_data:
                    param_id = param_data[0]
                    value_type = param_data[1]
                    value = param_data[2]
                    
                    if not value_type in VALUE_TYPES:
                        raise ValueError(f"Invalid value type: {value_type}")
                    
                    # convert the value to float
                    try:
                        value = float(value)
                    except ValueError:
                        value = None
                    
                    params_data[param_id] = value
                
                data.get("values").append(params_data)
        
        return data
