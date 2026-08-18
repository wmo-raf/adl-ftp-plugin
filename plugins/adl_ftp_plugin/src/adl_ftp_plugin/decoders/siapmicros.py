from csv import reader as csv_reader
from datetime import datetime

from ..registries import FTPDecoder

VALUE_TYPES = {
    "A": "Instantaneous",
    "B": "Average",
    "C": "Minimum",
    "D": "Maximum",
}


class SiapMicrosDecoder(FTPDecoder):
    """
    This class represents a decoder for the SIAP+Micros data format.
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

            # parse line by line
            for line in reader:
                check_field = line[len(line) - 1]
                if not check_field.startswith("#"):
                    raise ValueError("The last field of the line should start with a '#' character.")

                # check count
                count = int(check_field[1:])
                if not len(line) == count:
                    raise ValueError("The count does not match the number of fields. "
                                     "Expected: {0}, Actual: {1}".format(count, len(line))
                                     )

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
                    raise ValueError(f"The number of blocks data found :{len(blocks_units_data)} is not "
                                     f"equal to the number of expected blocks: {num_of_blocks}")

                params_data = {
                    "station_id": station_id,
                    "observation_time": obs_date,
                }

                for param_data in blocks_units_data:
                    param_id = param_data[0]
                    value_type = param_data[1]
                    value = param_data[2]

                    if value_type not in VALUE_TYPES:
                        raise ValueError(f"Invalid value type: {value_type}")

                    # convert the value to float
                    try:
                        value = float(value)
                    except ValueError:
                        value = None

                    param_data_id = f"{param_id}_{value_type}"

                    params_data[param_data_id] = value

                data.get("values").append(params_data)

        return data
