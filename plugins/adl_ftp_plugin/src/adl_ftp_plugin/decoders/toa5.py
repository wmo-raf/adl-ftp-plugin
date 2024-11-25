from csv import reader as csv_reader
from datetime import datetime

from ..registries import FTPDecoder


class Toa5Decoder(FTPDecoder):
    """
    This class represents a decoder for the TOA5 data format.
    """
    
    type = "toa5"
    compat_type = "campbell"
    display_name = "TOA5"
    
    def decode(self, file_path):
        """
        Decodes the given file and returns the result.

        :param file_path: The file that should be decoded.
        :type file_path: str
        :return: The decoded data.
        :rtype: dict
        """
        
        with open(file_path, "r", encoding="UTF-8") as f_in:
            reader = csv_reader(line.replace('\0', '') for line in f_in)
            
            # get header info
            first_line = next(reader)
            header_info = self.parse_header(first_line)
            
            # column names
            column_names = next(reader)
            
            # units
            units_list = next(reader)
            # the number of columns and units should match
            if not len(column_names) == len(units_list):
                raise ValueError("The number of columns and units do not match.")
            
            processing_info_list = next(reader)
            if not len(processing_info_list) == len(column_names):
                raise ValueError("The number of processing info fields and columns do not match.")
            
            metadata = {}
            for i, column in enumerate(column_names):
                metadata[column] = {
                    "unit": units_list[i],
                    "proc": processing_info_list[i],
                }
            
            data_values = self.parse_data(column_names, reader)
        
        data = {
            "header": header_info,
            "metadata": metadata,
            "values": data_values,
        }
        
        return data
    
    @staticmethod
    def parse_header(first_line):
        """
        Parses the first line of the file and returns the result.

        :param first_line: The first line of the file.
        :type first_line: list
        :return: The parsed data.
        :rtype: dict
        """
        
        if not first_line[0] == "TOA5":
            raise ValueError("The file format is not TOA5.")
        
        if not len(first_line) == 8:
            raise ValueError("The header does not contain the required number of fields.")
        
        header_info = {
            "format": first_line[0],
            "station_id": first_line[1],
            "datalogger_type": first_line[2],
            "serial_number": first_line[3],
            "os_version": first_line[4],
            "dld_name": first_line[5],
            "dld_signature": first_line[6],
            "table_name": first_line[7],
        }
        
        return header_info
    
    @staticmethod
    def parse_data(column_names, data_lines):
        """
        Parses the data lines and returns the result.

        :param column_names: The column names.
        :type column_names: list
        
        :param data_lines: The data lines.
        :type data_lines: iterable
        
        :return: The parsed data.
        :rtype: list
        """
        
        data = []
        
        for line in data_lines:
            line_data = {}
            
            for i, column in enumerate(column_names):
                val = line[i]
                if not val:
                    continue
                
                if column == 'TIMESTAMP':
                    line_data[column] = datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
                else:
                    line_data[column] = float(val)
            
            data.append(line_data)
        
        return data
