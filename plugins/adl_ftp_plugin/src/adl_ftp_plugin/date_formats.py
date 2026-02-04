from django.utils.translation import gettext_lazy as _

FILENAME_DATE_FORMAT_DEFINITIONS = [
    # Compact formats (no separators)
    {
        "format": "YYYYMMDD",
        "label": _("YYYYMMDD - e.g., 20250115"),
        "has_time": False,
        "length": 8,
        "strptime": "%Y%m%d",
    },
    {
        "format": "YYYYMMDDHH",
        "label": _("YYYYMMDDHH - e.g., 2025011514"),
        "has_time": True,
        "length": 10,
        "strptime": "%Y%m%d%H",
    },
    {
        "format": "YYYYMMDDHHMM",
        "label": _("YYYYMMDDHHMM - e.g., 202501151430"),
        "has_time": True,
        "length": 12,
        "strptime": "%Y%m%d%H%M",
    },
    {
        "format": "YYYYMMDDHHMMSS",
        "label": _("YYYYMMDDHHMMSS - e.g., 20250115143045"),
        "has_time": True,
        "length": 14,
        "strptime": "%Y%m%d%H%M%S",
    },
    {
        "format": "YYMMDD",
        "label": _("YYMMDD - e.g., 250115"),
        "has_time": False,
        "length": 6,
        "strptime": "%y%m%d",
    },
    {
        "format": "YYMMDDHHMM",
        "label": _("YYMMDDHHMM - e.g., 2501151430"),
        "has_time": True,
        "length": 10,
        "strptime": "%y%m%d%H%M",
    },
    {
        "format": "DDMMYYYY",
        "label": _("DDMMYYYY - e.g., 15012025"),
        "has_time": False,
        "length": 8,
        "strptime": "%d%m%Y",
    },
    {
        "format": "MMDDYYYY",
        "label": _("MMDDYYYY - e.g., 01152025"),
        "has_time": False,
        "length": 8,
        "strptime": "%m%d%Y",
    },
    {
        "format": "DDMMYY",
        "label": _("DDMMYY - e.g., 150125"),
        "has_time": False,
        "length": 6,
        "strptime": "%d%m%y",
    },
    {
        "format": "MMDDYY",
        "label": _("MMDDYY - e.g., 011525"),
        "has_time": False,
        "length": 6,
        "strptime": "%m%d%y",
    },
    
    # Underscore separated compact datetime
    {
        "format": "YYYYMMDD_HHMMSS",
        "label": _("YYYYMMDD_HHMMSS - e.g., 20250115_143045"),
        "has_time": True,
        "length": 15,
        "strptime": "%Y%m%d_%H%M%S",
    },
    
    # Dash separated
    {
        "format": "YYYY-MM-DD",
        "label": _("YYYY-MM-DD - e.g., 2025-01-15"),
        "has_time": False,
        "length": 10,
        "strptime": "%Y-%m-%d",
    },
    {
        "format": "YYYY-MM-DD-HH",
        "label": _("YYYY-MM-DD-HH - e.g., 2025-01-15-14"),
        "has_time": True,
        "length": 13,
        "strptime": "%Y-%m-%d-%H",
    },
    {
        "format": "YYYY-MM-DD-HHMM",
        "label": _("YYYY-MM-DD-HHMM - e.g., 2025-01-15-1430"),
        "has_time": True,
        "length": 15,
        "strptime": "%Y-%m-%d-%H%M",
    },
    {
        "format": "YYYY-MM-DD-HHMMSS",
        "label": _("YYYY-MM-DD-HHMMSS - e.g., 2025-01-15-143045"),
        "has_time": True,
        "length": 17,
        "strptime": "%Y-%m-%d-%H%M%S",
    },
    {
        "format": "DD-MM-YYYY",
        "label": _("DD-MM-YYYY - e.g., 15-01-2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%d-%m-%Y",
    },
    {
        "format": "MM-DD-YYYY",
        "label": _("MM-DD-YYYY - e.g., 01-15-2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%m-%d-%Y",
    },
    {
        "format": "YY-MM-DD",
        "label": _("YY-MM-DD - e.g., 25-01-15"),
        "has_time": False,
        "length": 8,
        "strptime": "%y-%m-%d",
    },
    
    # Underscore separated
    {
        "format": "YYYY_MM_DD",
        "label": _("YYYY_MM_DD - e.g., 2025_01_15"),
        "has_time": False,
        "length": 10,
        "strptime": "%Y_%m_%d",
    },
    {
        "format": "YYYY_MM_DD_HH",
        "label": _("YYYY_MM_DD_HH - e.g., 2025_01_15_14"),
        "has_time": True,
        "length": 13,
        "strptime": "%Y_%m_%d_%H",
    },
    {
        "format": "YYYY_MM_DD_HHMM",
        "label": _("YYYY_MM_DD_HHMM - e.g., 2025_01_15_1430"),
        "has_time": True,
        "length": 15,
        "strptime": "%Y_%m_%d_%H%M",
    },
    {
        "format": "YYYY_MM_DD_HHMMSS",
        "label": _("YYYY_MM_DD_HHMMSS - e.g., 2025_01_15_143045"),
        "has_time": True,
        "length": 17,
        "strptime": "%Y_%m_%d_%H%M%S",
    },
    {
        "format": "DD_MM_YYYY",
        "label": _("DD_MM_YYYY - e.g., 15_01_2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%d_%m_%Y",
    },
    {
        "format": "MM_DD_YYYY",
        "label": _("MM_DD_YYYY - e.g., 01_15_2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%m_%d_%Y",
    },
    
    # Dot separated
    {
        "format": "YYYY.MM.DD",
        "label": _("YYYY.MM.DD - e.g., 2025.01.15"),
        "has_time": False,
        "length": 10,
        "strptime": "%Y.%m.%d",
    },
    {
        "format": "DD.MM.YYYY",
        "label": _("DD.MM.YYYY - e.g., 15.01.2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%d.%m.%Y",
    },
    {
        "format": "MM.DD.YYYY",
        "label": _("MM.DD.YYYY - e.g., 01.15.2025"),
        "has_time": False,
        "length": 10,
        "strptime": "%m.%d.%Y",
    },
    
    # ISO 8601 / RFC 3339 variants
    {
        "format": "YYYY-MM-DDTHH",
        "label": _("YYYY-MM-DDTHH - e.g., 2025-01-15T14"),
        "has_time": True,
        "length": 13,
        "strptime": "%Y-%m-%dT%H",
    },
    {
        "format": "YYYY-MM-DDTHHMMSS",
        "label": _("YYYY-MM-DDTHHMMSS - e.g., 2025-01-15T143045"),
        "has_time": True,
        "length": 17,
        "strptime": "%Y-%m-%dT%H%M%S",
    },
    {
        "format": "YYYY-MM-DDTHH:MM:SS",
        "label": _("YYYY-MM-DDTHH:MM:SS - e.g., 2025-01-15T14:30:45"),
        "has_time": True,
        "length": 19,
        "strptime": "%Y-%m-%dT%H:%M:%S",
    },
    
    # Julian day
    {
        "format": "YYYYDDD",
        "label": _("YYYYDDD - e.g., 2025015 (Julian day)"),
        "has_time": False,
        "length": 7,
        "strptime": "%Y%j",
    },
    {
        "format": "YYDDD",
        "label": _("YYDDD - e.g., 25015 (Julian day)"),
        "has_time": False,
        "length": 5,
        "strptime": "%y%j",
    },
    
    # Year and month only
    {
        "format": "YYYYMM",
        "label": _("YYYYMM - e.g., 202501"),
        "has_time": False,
        "length": 6,
        "strptime": "%Y%m",
    },
    {
        "format": "YYYY-MM",
        "label": _("YYYY-MM - e.g., 2025-01"),
        "has_time": False,
        "length": 7,
        "strptime": "%Y-%m",
    },
    {
        "format": "YYYY_MM",
        "label": _("YYYY_MM - e.g., 2025_01"),
        "has_time": False,
        "length": 7,
        "strptime": "%Y_%m",
    },
    
    # Text month formats
    {
        "format": "YYYY-MMM-DD",
        "label": _("YYYY-MMM-DD - e.g., 2025-Jan-15"),
        "has_time": False,
        "length": 11,
        "strptime": "%Y-%b-%d",
    },
    {
        "format": "DD-MMM-YYYY",
        "label": _("DD-MMM-YYYY - e.g., 15-Jan-2025"),
        "has_time": False,
        "length": 11,
        "strptime": "%d-%b-%Y",
    },
    {
        "format": "YYYYMMMDD",
        "label": _("YYYYMMMDD - e.g., 2025Jan15"),
        "has_time": False,
        "length": 9,
        "strptime": "%Y%b%d",
    },
    {
        "format": "DDMMMYYYY",
        "label": _("DDMMMYYYY - e.g., 15Jan2025"),
        "has_time": False,
        "length": 9,
        "strptime": "%d%b%Y",
    },
    
    # Unix timestamp
    {
        "format": "TIMESTAMP",
        "label": _("Unix Timestamp - e.g., 1705329600"),
        "has_time": True,
        "length": 10,
        "strptime": None,  # handled separately
    },
]

# Derived structures
FILENAME_DATE_FORMAT_CHOICES = [(d["format"], d["label"]) for d in FILENAME_DATE_FORMAT_DEFINITIONS]
TIME_INCLUSIVE_FORMATS = frozenset(d["format"] for d in FILENAME_DATE_FORMAT_DEFINITIONS if d["has_time"])

# Lookup dict for parsing
_FORMAT_LOOKUP = {d["format"]: d for d in FILENAME_DATE_FORMAT_DEFINITIONS}


def format_has_time_component(date_format):
    """Check if a date format includes time information."""
    return date_format in TIME_INCLUSIVE_FORMATS


def get_format_definition(date_format):
    """Get the full definition for a date format."""
    return _FORMAT_LOOKUP.get(date_format)
