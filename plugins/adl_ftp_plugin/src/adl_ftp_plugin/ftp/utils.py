import datetime
import re

from dateutil import parser


def _get_year(date):
    from dateutil.relativedelta import relativedelta
    
    current_date = datetime.datetime.now()
    parsed_date = parser.parse("%s" % date)
    if current_date > parsed_date:
        current = current_date
    else:
        current = current_date - relativedelta(years=1)
    return current.strftime('%Y')


class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def split_file_info(fileinfo):
    """ Parse sane directory output usually ls -l
        Adapted from https://gist.github.com/tobiasoberrauch/2942716
    """
    files = []
    
    unix_format = re.compile(
        r'^([\-dbclps])' +  # Directory flag [1]
        r'((?:[r-][w-][-xsStT]){3})\s+' +  # Permissions [2]
        r'(\d+)\s+' +  # Number of items [3]
        r'([a-zA-Z0-9_-]+)\s+' +  # File owner [4]
        r'([a-zA-Z0-9_-]+)\s+' +  # File group [5]
        r'(\d+)\s+' +  # File size in bytes [6]
        r'(\w{3}\s+\d{1,2})\s+' +  # 3-char month and 1/2-char day of the month [7]
        r'(\d{1,2}:\d{1,2}|\d{4})\s+' +  # Time or year (need to check conditions) [+= 7]
        r'(.+)$'  # File/directory name [8]
    )
    
    # not exactly sure what format this, but seems windows-esque
    # attempting to address issue: https://github.com/codebynumbers/ftpretty/issues/34
    # can get better results with more data.
    windows_format = re.compile(
        r'(\d{2})-(\d{2})-(\d{2})\s+' +  # month/day/2-digit year (assuming after 2000)
        r'(\d{2}):(\d{2})([AP])M\s+' +  # time
        r'(\d+)\s+' +  # file size
        r'(.+)$'  # filename
    )
    
    for line in fileinfo:
        if unix_format.match(line):
            parts = unix_format.split(line)
            
            date = parts[7]
            time = parts[8] if ':' in parts[8] else '00:00'
            year = parts[8] if ':' not in parts[8] else _get_year(date)
            dt_obj = parser.parse("%s %s %s" % (date, year, time))
            
            files.append(dotdict({
                'directory': parts[1],
                'flags': parts[1],
                'perms': parts[2],
                'items': parts[3],
                'owner': parts[4],
                'group': parts[5],
                'size': int(parts[6]),
                'date': date,
                'time': time,
                'year': year,
                'name': parts[9],
                'datetime': dt_obj
            }))
        
        elif windows_format.match(line):
            parts = windows_format.split(line)
            
            hour = int(parts[4])
            hour += 12 if parts[6] == 'P' else 0
            hour = 0 if hour == 24 else hour
            year = int(parts[3]) + 2000
            dt_obj = datetime.datetime(year, int(parts[1]), int(parts[2]), hour, int(parts[5]), 0)
            
            files.append(dotdict({
                'directory': None,
                'flags': None,
                'perms': None,
                'items': None,
                'owner': None,
                'group': None,
                'size': int(parts[7]),
                'date': "{}-{}-{}".format(*parts[1:4]),
                'time': "{}:{}{}".format(*parts[4:7]),
                'year': year,
                'name': parts[8],
                'datetime': dt_obj
            }))
    
    return files
