from django.utils.translation import gettext_lazy as _

FILENAME_DATE_FORMAT_DEFINITIONS = [
    # Compact formats (no separators)
    {
        "format": "YYYYMMDD",
        "label": _("YYYYMMDD - e.g., 20250115"),
        "has_time": False,
    },
    {
        "format": "YYYYMMDDHH",
        "label": _("YYYYMMDDHH - e.g., 2025011514"),
        "has_time": True,
    },
    {
        "format": "YYYYMMDDHHMM",
        "label": _("YYYYMMDDHHMM - e.g., 202501151430"),
        "has_time": True,
    },
    {
        "format": "YYYYMMDDHHMMSS",
        "label": _("YYYYMMDDHHMMSS - e.g., 20250115143045"),
        "has_time": True,
    },
    {
        "format": "YYMMDD",
        "label": _("YYMMDD - e.g., 250115"),
        "has_time": False,
    },
    {
        "format": "YYMMDDHHMM",
        "label": _("YYMMDDHHMM - e.g., 2501151430"),
        "has_time": True,
    },
    {
        "format": "DDMMYYYY",
        "label": _("DDMMYYYY - e.g., 15012025"),
        "has_time": False,
    },
    {
        "format": "MMDDYYYY",
        "label": _("MMDDYYYY - e.g., 01152025"),
        "has_time": False,
    },
    {
        "format": "DDMMYY",
        "label": _("DDMMYY - e.g., 150125"),
        "has_time": False,
    },
    {
        "format": "MMDDYY",
        "label": _("MMDDYY - e.g., 011525"),
        "has_time": False,
    },
    
    # Underscore separated compact datetime
    {
        "format": "YYYYMMDD_HHMMSS",
        "label": _("YYYYMMDD_HHMMSS - e.g., 20250115_143045"),
        "has_time": True,
    },
    
    # Dash separated
    {
        "format": "YYYY-MM-DD",
        "label": _("YYYY-MM-DD - e.g., 2025-01-15"),
        "has_time": False,
    },
    {
        "format": "YYYY-MM-DD-HH",
        "label": _("YYYY-MM-DD-HH - e.g., 2025-01-15-14"),
        "has_time": True,
    },
    {
        "format": "YYYY-MM-DD-HHMM",
        "label": _("YYYY-MM-DD-HHMM - e.g., 2025-01-15-1430"),
        "has_time": True,
    },
    {
        "format": "YYYY-MM-DD-HHMMSS",
        "label": _("YYYY-MM-DD-HHMMSS - e.g., 2025-01-15-143045"),
        "has_time": True,
    },
    {
        "format": "DD-MM-YYYY",
        "label": _("DD-MM-YYYY - e.g., 15-01-2025"),
        "has_time": False,
    },
    {
        "format": "MM-DD-YYYY",
        "label": _("MM-DD-YYYY - e.g., 01-15-2025"),
        "has_time": False,
    },
    {
        "format": "YY-MM-DD",
        "label": _("YY-MM-DD - e.g., 25-01-15"),
        "has_time": False,
    },
    
    # Underscore separated
    {
        "format": "YYYY_MM_DD",
        "label": _("YYYY_MM_DD - e.g., 2025_01_15"),
        "has_time": False,
    },
    {
        "format": "YYYY_MM_DD_HH",
        "label": _("YYYY_MM_DD_HH - e.g., 2025_01_15_14"),
        "has_time": True,
    },
    {
        "format": "YYYY_MM_DD_HHMM",
        "label": _("YYYY_MM_DD_HHMM - e.g., 2025_01_15_1430"),
        "has_time": True,
    },
    {
        "format": "YYYY_MM_DD_HHMMSS",
        "label": _("YYYY_MM_DD_HHMMSS - e.g., 2025_01_15_143045"),
        "has_time": True,
    },
    {
        "format": "DD_MM_YYYY",
        "label": _("DD_MM_YYYY - e.g., 15_01_2025"),
        "has_time": False,
    },
    {
        "format": "MM_DD_YYYY",
        "label": _("MM_DD_YYYY - e.g., 01_15_2025"),
        "has_time": False,
    },
    # Dot separated
    {
        "format": "YYYY.MM.DD",
        "label": _("YYYY.MM.DD - e.g., 2025.01.15"),
        "has_time": False,
    },
    {
        "format": "DD.MM.YYYY",
        "label": _("DD.MM.YYYY - e.g., 15.01.2025"),
        "has_time": False,
    },
    {
        "format": "MM.DD.YYYY",
        "label": _("MM.DD.YYYY - e.g., 01.15.2025"),
        "has_time": False,
    },
    
    # ISO 8601 / RFC 3339 variants
    {
        "format": "YYYY-MM-DDTHH",
        "label": _("YYYY-MM-DDTHH - e.g., 2025-01-15T14"),
        "has_time": True,
    },
    {
        "format": "YYYY-MM-DDTHHMMSS",
        "label": _("YYYY-MM-DDTHHMMSS - e.g., 2025-01-15T143045"),
        "has_time": True,
    },
    {
        "format": "YYYY-MM-DDTHH:MM:SS",
        "label": _("YYYY-MM-DDTHH:MM:SS - e.g., 2025-01-15T14:30:45"),
        "has_time": True,
    },
    # Julian day
    {
        "format": "YYYYDDD",
        "label": _("YYYYDDD - e.g., 2025015 (Julian day)"),
        "has_time": False,
    },
    {
        "format": "YYDDD",
        "label": _("YYDDD - e.g., 25015 (Julian day)"),
        "has_time": False,
    },
    # Year and month only
    {
        "format": "YYYYMM",
        "label": _("YYYYMM - e.g., 202501"),
        "has_time": False,
    },
    {
        "format": "YYYY-MM",
        "label": _("YYYY-MM - e.g., 2025-01"),
        "has_time": False,
    },
    {
        "format": "YYYY_MM",
        "label": _("YYYY_MM - e.g., 2025_01"),
        "has_time": False,
    },
    # Text month formats
    {
        "format": "YYYY-MMM-DD",
        "label": _("YYYY-MMM-DD - e.g., 2025-Jan-15"),
        "has_time": False,
    },
    {
        "format": "DD-MMM-YYYY",
        "label": _("DD-MMM-YYYY - e.g., 15-Jan-2025"),
        "has_time": False,
    },
    {
        "format": "YYYYMMMDD",
        "label": _("YYYYMMMDD - e.g., 2025Jan15"),
        "has_time": False,
    },
    {
        "format": "DDMMMYYYY",
        "label": _("DDMMMYYYY - e.g., 15Jan2025"),
        "has_time": False,
    },
    # Unix timestamp
    {
        "format": "TIMESTAMP",
        "label": _("Unix Timestamp - e.g., 1705329600"),
        "has_time": True,
    },
]

# Derived structures
FILENAME_DATE_FORMAT_CHOICES = [(d["format"], d["label"]) for d in FILENAME_DATE_FORMAT_DEFINITIONS]
TIME_INCLUSIVE_FORMATS = frozenset(d["format"] for d in FILENAME_DATE_FORMAT_DEFINITIONS if d["has_time"])


def format_has_time_component(date_format):
    """Check if a date format includes time information."""
    return date_format in TIME_INCLUSIVE_FORMATS
