import logging
from csv import reader as csv_reader
from datetime import datetime

from ..registries import FTPDecoder

logger = logging.getLogger(__name__)


class StandardCSVDecoder(FTPDecoder):
    """
    Decoder for standard CSV files with configurable datetime columns.
    """

    type = "standard_csv"
    compat_type = "standard_csv"
    display_name = "Standard CSV"
    requires_config = True

    def decode(self, file_path, config=None):
        """
        Decodes a standard CSV file based on configuration.

        :param file_path: Path to the CSV file
        :param config: The ``StandardCSVConfig`` to decode with. Callers that
            resolved this decoder through ``decoder_resolution`` always pass
            one; the ``_config`` fallback is only for a caller that predates
            the argument and still assigns the config to the instance.
        :return: Dictionary with 'values' key containing list of records
        """
        if config is None:
            config = getattr(self, '_config', None)
        if not config:
            raise ValueError("CSV configuration not set. Ensure csv_config is configured in NetworkFTP.")

        data = {"values": []}

        with open(file_path, "r", encoding="UTF-8") as f_in:
            # Handle delimiter from config
            delimiter = config.delimiter if config.delimiter else ","
            reader = csv_reader(
                (line.replace('\0', '') for line in f_in),
                delimiter=delimiter
            )

            # Skip rows if configured
            for _ in range(config.skip_rows):
                try:
                    next(reader)
                except StopIteration:
                    logger.warning(f"Attempted to skip {config.skip_rows} rows but file has fewer rows")
                    return data

            # Read or generate header
            first_row = None
            if config.has_header:
                # Read header from file
                try:
                    header = next(reader)
                except StopIteration:
                    logger.warning("CSV file is empty or has no data after skipped rows")
                    return data
            else:
                # No header - peek at first data row to determine number of columns
                try:
                    first_row = next(reader)
                    num_columns = len(first_row)
                    # Generate column names: column_1, column_2, etc.
                    header = [f"column_{i + 1}" for i in range(num_columns)]
                    logger.debug(f"Generated header for headerless CSV: {header}")
                except StopIteration:
                    logger.warning("CSV file is empty")
                    return data

            # Validate required columns exist
            self._validate_columns(header, config)

            # Process data rows
            if config.has_header:
                # Normal processing - all data rows are after header
                for line_num, line in enumerate(reader, start=config.skip_rows + 2):
                    try:
                        record = self._parse_row(header, line, config)
                        if record:
                            data["values"].append(record)
                    except Exception as e:
                        logger.warning(
                            f"Error parsing CSV row {line_num}: {e}. Skipping row."
                        )
                        continue
            else:
                # No header - process first row that was already read
                try:
                    record = self._parse_row(header, first_row, config)
                    if record:
                        data["values"].append(record)
                except Exception as e:
                    logger.warning(f"Error parsing CSV first row: {e}. Skipping row.")

                # Process remaining rows
                for line_num, line in enumerate(reader, start=config.skip_rows + 2):
                    try:
                        record = self._parse_row(header, line, config)
                        if record:
                            data["values"].append(record)
                    except Exception as e:
                        logger.warning(
                            f"Error parsing CSV row {line_num}: {e}. Skipping row."
                        )
                        continue

        return data

    def _validate_columns(self, header, config):
        """Validate that required columns exist in the CSV header"""
        if config.datetime_mode == "single":
            if config.datetime_column not in header:
                raise ValueError(
                    f"Datetime column '{config.datetime_column}' not found in CSV. "
                    f"Available columns: {', '.join(header)}"
                )
        else:  # separate
            missing_columns = []
            if config.date_column not in header:
                missing_columns.append(f"date column '{config.date_column}'")
            if config.time_column not in header:
                missing_columns.append(f"time column '{config.time_column}'")

            if missing_columns:
                raise ValueError(
                    f"{' and '.join(missing_columns)} not found in CSV. "
                    f"Available columns: {', '.join(header)}"
                )

    def _parse_row(self, header, row, config):
        """Parse a single CSV row into a record dictionary"""
        # Skip empty rows
        if not row or all(not cell.strip() for cell in row):
            return None

        # Handle rows with different number of columns than header
        if len(row) != len(header):
            logger.warning(
                f"Row has {len(row)} columns but header has {len(header)} columns. "
                f"Attempting to parse anyway."
            )
            # Pad with empty strings if row is shorter
            row = row + [''] * (len(header) - len(row))

        # Create dict from row
        row_dict = dict(zip(header, row))

        # Parse datetime
        try:
            if config.datetime_mode == "single":
                datetime_str = row_dict.get(config.datetime_column, "").strip()
                if not datetime_str:
                    logger.debug(f"Empty datetime value in column '{config.datetime_column}'. Skipping row.")
                    return None
                obs_time = datetime.strptime(datetime_str, config.datetime_format)
            else:  # separate
                date_str = row_dict.get(config.date_column, "").strip()
                time_str = row_dict.get(config.time_column, "").strip()

                if not date_str or not time_str:
                    logger.debug(
                        f"Empty date or time value (date: '{date_str}', time: '{time_str}'). "
                        f"Skipping row."
                    )
                    return None

                date_obj = datetime.strptime(date_str, config.date_format)
                time_obj = datetime.strptime(time_str, config.time_format)

                # Combine date and time
                obs_time = datetime.combine(date_obj.date(), time_obj.time())
        except ValueError as e:
            raise ValueError(f"Error parsing datetime: {e}")

        # Build record
        record = {"observation_time": obs_time}

        # Add all other columns as parameters
        exclude_columns = {
            config.datetime_column if config.datetime_mode == "single" else None,
            config.date_column if config.datetime_mode == "separate" else None,
            config.time_column if config.datetime_mode == "separate" else None,
        }
        exclude_columns.discard(None)

        for col_name, value in row_dict.items():
            if col_name not in exclude_columns:
                value = value.strip()
                if value:  # Only add non-empty values
                    try:
                        # Try to convert to float
                        float_val = float(value)
                        record[col_name] = float(value)
                        # check for No data value
                        if config.no_data_value is not None and config.no_data_value == float_val:
                            record[col_name] = None

                    except ValueError:
                        # Keep as string if not numeric
                        record[col_name] = value

        return record
