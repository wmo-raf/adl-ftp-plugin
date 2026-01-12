import logging

from adl.core.models import DataParameter, Unit
from adl.core.models import NetworkConnection, StationLink, DispatchChannel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from timezone_field import TimeZoneField
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from adl_ftp_plugin.dispatchers.ftp import dispatch_to_ftp
from adl_ftp_plugin.ftp import FTPClient
from adl_ftp_plugin.ftp.sftp import SFTPClient
from adl_ftp_plugin.utils import get_ftp_decoder_choices
from adl_ftp_plugin.validators import validate_start_date
from .smartmet_utils import get_station_metadata_csv
from .widgets import (
    ConnectionTypeRadioSelect,
    ConditionalRadioSelect,
    FTPDirectoryTreeSelectWidget,
    FTPDecoderSelectWidget
)

logger = logging.getLogger(__name__)


class ConnectionType(models.TextChoices):
    """Connection type choices"""
    FTP = 'ftp', _('FTP')
    FTPS = 'ftps', _('FTPS (FTP over TLS)')
    SFTP = 'sftp', _('SFTP (SSH File Transfer)')


class HostKeyPolicy(models.TextChoices):
    """SFTP host key policy choices"""
    AUTO = 'auto', _('Auto-accept (less secure)')
    WARN = 'warn', _('Warn but connect')
    REJECT = 'reject', _('Reject unknown hosts (most secure)')


class StandardCSVConfig(models.Model):
    """Configuration for standard CSV file format"""
    
    DATETIME_MODE_CHOICES = [
        ("single", _("Single datetime column")),
        ("separate", _("Separate date and time columns")),
    ]
    
    # Common datetime format choices
    DATETIME_FORMAT_CHOICES = [
        ("%Y-%m-%d %H:%M:%S", "2025-01-15 14:30:45 (YYYY-MM-DD HH:MM:SS - ISO 8601)"),
        ("%Y-%m-%d %H:%M:%S.%f", "2025-01-15 14:30:45.123456 (with microseconds)"),
        ("%Y-%m-%dT%H:%M:%S", "2025-01-15T14:30:45 (ISO 8601 with T separator)"),
        ("%Y-%m-%dT%H:%M:%SZ", "2025-01-15T14:30:45Z (ISO 8601 UTC)"),
        ("%Y-%m-%d %H:%M", "2025-01-15 14:30 (YYYY-MM-DD HH:MM - no seconds)"),
        ("%d/%m/%Y %H:%M:%S", "15/01/2025 14:30:45 (DD/MM/YYYY HH:MM:SS)"),
        ("%d/%m/%Y %H:%M", "15/01/2025 14:30 (DD/MM/YYYY HH:MM - no seconds)"),
        ("%m/%d/%Y %H:%M:%S", "01/15/2025 14:30:45 (MM/DD/YYYY HH:MM:SS)"),
        ("%m/%d/%Y %H:%M", "01/15/2025 14:30 (MM/DD/YYYY HH:MM - no seconds)"),
        ("%d-%m-%Y %H:%M:%S", "15-01-2025 14:30:45 (DD-MM-YYYY HH:MM:SS)"),
        ("%m-%d-%Y %H:%M:%S", "01-15-2025 14:30:45 (MM-DD-YYYY HH:MM:SS)"),
        ("%Y%m%d%H%M%S", "20250115143045 (YYYYMMDDHHMMSS - compact)"),
        ("%Y%m%d%H%M", "202501151430 (YYYYMMDDHHMM - compact no seconds)"),
        ("%Y%m%d %H%M%S", "20250115 143045 (YYYYMMDD HHMMSS - compact with space)"),
        ("%Y%m%d %H%M", "20250115 1430 (YYYYMMDD HHMM - compact no seconds with space)"),
        ("%d.%m.%Y %H:%M:%S", "15.01.2025 14:30:45 (DD.MM.YYYY HH:MM:SS)"),
        ("%d.%m.%Y %H:%M", "15.01.2025 14:30 (DD.MM.YYYY HH:MM - no seconds)"),
        ("%Y/%m/%d %H:%M:%S", "2025/01/15 14:30:45 (YYYY/MM/DD HH:MM:SS)"),
        ("%d %b %Y %H:%M:%S", "15 Jan 2025 14:30:45 (DD Mon YYYY HH:MM:SS)"),
        ("%d %B %Y %H:%M:%S", "15 January 2025 14:30:45 (DD Month YYYY HH:MM:SS)"),
        ("%Y-%m-%d %I:%M:%S %p", "2025-01-15 02:30:45 PM (YYYY-MM-DD 12-hour with AM/PM)"),
        ("%m/%d/%Y %I:%M:%S %p", "01/15/2025 02:30:45 PM (MM/DD/YYYY 12-hour with AM/PM)"),
        ("%d/%m/%Y %I:%M:%S %p", "15/01/2025 02:30:45 PM (DD/MM/YYYY 12-hour with AM/PM)"),
        ("%a, %d %b %Y %H:%M:%S", "Wed, 15 Jan 2025 14:30:45 (RFC 2822)"),
    ]
    
    # Common date format choices
    DATE_FORMAT_CHOICES = [
        ("%Y-%m-%d", "2025-01-15 (YYYY-MM-DD - ISO 8601)"),
        ("%d/%m/%Y", "15/01/2025 (DD/MM/YYYY)"),
        ("%m/%d/%Y", "01/15/2025 (MM/DD/YYYY)"),
        ("%d-%m-%Y", "15-01-2025 (DD-MM-YYYY)"),
        ("%m-%d-%Y", "01-15-2025 (MM-DD-YYYY)"),
        ("%Y/%m/%d", "2025/01/15 (YYYY/MM/DD)"),
        ("%d.%m.%Y", "15.01.2025 (DD.MM.YYYY)"),
        ("%Y%m%d", "20250115 (YYYYMMDD - compact)"),
        ("%d %b %Y", "15 Jan 2025 (DD Mon YYYY - abbreviated month)"),
        ("%d %B %Y", "15 January 2025 (DD Month YYYY - full month)"),
        ("%b %d, %Y", "Jan 15, 2025 (Mon DD, YYYY)"),
        ("%B %d, %Y", "January 15, 2025 (Month DD, YYYY)"),
        ("%d-%b-%Y", "15-Jan-2025 (DD-Mon-YYYY)"),
        ("%Y.%m.%d", "2025.01.15 (YYYY.MM.DD)"),
        ("%d/%m/%y", "15/01/25 (DD/MM/YY - 2-digit year)"),
        ("%m/%d/%y", "01/15/25 (MM/DD/YY - 2-digit year)"),
        ("%y-%m-%d", "25-01-15 (YY-MM-DD - 2-digit year)"),
        ("%y%m%d", "250115 (YYMMDD - compact 2-digit year)"),
    ]
    
    # Common time format choices
    TIME_FORMAT_CHOICES = [
        ("%H:%M:%S", "14:30:45 (HH:MM:SS - 24-hour with seconds)"),
        ("%H:%M", "14:30 (HH:MM - 24-hour without seconds)"),
        ("%H:%M:%S.%f", "14:30:45.123456 (HH:MM:SS.microseconds)"),
        ("%I:%M:%S %p", "02:30:45 PM (12-hour with AM/PM and seconds)"),
        ("%I:%M %p", "02:30 PM (12-hour with AM/PM, no seconds)"),
        ("%H%M%S", "143045 (HHMMSS - compact 24-hour)"),
        ("%H%M", "1430 (HHMM - compact 24-hour, no seconds)"),
        ("%I:%M:%S%p", "02:30:45PM (12-hour, no space before AM/PM)"),
        ("%I:%M%p", "02:30PM (12-hour, no seconds, no space)"),
        ("%H.%M.%S", "14.30.45 (HH.MM.SS - dot separator)"),
        ("%H.%M", "14.30 (HH.MM - dot separator, no seconds)"),
    ]
    
    DELIMITER_CHOICES = [
        (",", "Comma (,)"),
        ("\t", "Tab (\\t)"),
        (";", "Semicolon (;)"),
        ("|", "Pipe (|)"),
        (" ", "Space ( )"),
        (":", "Colon (:)"),
    ]
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    
    name = models.CharField(max_length=255, verbose_name=_("Configuration Name"),
                            help_text=_("A descriptive name for this CSV configuration"))
    
    # CSV structure
    has_header = models.BooleanField(
        default=True,
        verbose_name=_("File has header row"),
        help_text=_("Check if the first row (after skipped rows) contains column names. "
                    "If unchecked, columns will be named as column_1, column_2, etc.")
    )
    
    # Datetime configuration
    datetime_mode = models.CharField(
        max_length=20,
        choices=DATETIME_MODE_CHOICES,
        default="single",
        verbose_name=_("Datetime Mode"),
        help_text=_("Whether datetime is in one column or split into date and time columns")
    )
    
    # Single column mode
    datetime_column = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Datetime Column Name"),
        help_text=_("Name of the column containing datetime (for single column mode)")
    )
    datetime_format = models.CharField(
        max_length=255,
        blank=True,
        choices=DATETIME_FORMAT_CHOICES,
        default="%Y-%m-%d %H:%M:%S",
        verbose_name=_("Datetime Format"),
        help_text=_("Select the format that matches your datetime column")
    )
    
    # Separate columns mode
    date_column = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Date Column Name"),
        help_text=_("Name of the column containing date (for separate columns mode)")
    )
    date_format = models.CharField(
        max_length=255,
        blank=True,
        choices=DATE_FORMAT_CHOICES,
        default="%Y-%m-%d",
        verbose_name=_("Date Format"),
        help_text=_("Select the format that matches your date column")
    )
    time_column = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Time Column Name"),
        help_text=_("Name of the column containing time (for separate columns mode)")
    )
    time_format = models.CharField(
        max_length=255,
        blank=True,
        choices=TIME_FORMAT_CHOICES,
        default="%H:%M:%S",
        verbose_name=_("Time Format"),
        help_text=_("Select the format that matches your time column")
    )
    
    # CSV parsing options
    delimiter = models.CharField(
        max_length=5,
        choices=DELIMITER_CHOICES,
        default=",",
        verbose_name=_("CSV Delimiter"),
        help_text=_("Character used to separate values")
    )
    skip_rows = models.IntegerField(
        default=0,
        verbose_name=_("Skip Rows"),
        help_text=_("Number of rows to skip before the header row")
    )
    
    panels = [
        FieldPanel("name"),
        MultiFieldPanel([
            FieldPanel("has_header"),
            FieldPanel("delimiter"),
            FieldPanel("skip_rows"),
        ], heading=_("CSV Structure")),
        MultiFieldPanel([
            FieldPanel("datetime_mode", widget=ConditionalRadioSelect),
        ], heading=_("Datetime Configuration")),
        MultiFieldPanel([
            FieldPanel("datetime_column"),
            FieldPanel("datetime_format"),
        ], heading=_("Single Column Mode Settings")),
        MultiFieldPanel([
            FieldPanel("date_column"),
            FieldPanel("date_format"),
            FieldPanel("time_column"),
            FieldPanel("time_format"),
        ], heading=_("Separate Columns Mode Settings")),
    ]
    
    class Meta:
        verbose_name = _("CSV Decoder Configuration")
        verbose_name_plural = _("CSV Decoder Configurations")
    
    def __str__(self):
        return self.name
    
    def clean(self):
        """Validate configuration based on datetime mode"""
        super().clean()
        
        if self.datetime_mode == "single":
            if not self.datetime_column:
                raise ValidationError({
                    'datetime_column': _("Datetime column name is required for single column mode")
                })
        elif self.datetime_mode == "separate":
            if not self.date_column or not self.time_column:
                raise ValidationError({
                    'date_column': _("Date column name is required for separate columns mode"),
                    'time_column': _("Time column name is required for separate columns mode")
                })


class NetworkFTP(NetworkConnection):
    """Flexible network connection supporting FTP, FTPS, and SFTP"""
    station_link_model_string_label = "adl_ftp_plugin.FTPStationLink"
    
    # Basic connection details
    connection_type = models.CharField(
        max_length=10,
        choices=ConnectionType.choices,
        default=ConnectionType.FTP,
        verbose_name=_("Connection Type"),
        help_text=_("Protocol to use for file transfer")
    )
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.IntegerField(verbose_name=_("Port"), blank=True, null=True,
                               help_text=_("Leave blank for default ports (21 for FTP/FTPS, 22 for SFTP)"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"), blank=True,
                                help_text=_("Required for FTP/FTPS, optional for SFTP with key auth"))
    
    # FTP/FTPS specific settings
    passive_mode = models.BooleanField(default=True, verbose_name=_("Use FTP Passive mode"),
                                       help_text=_("Only applies to FTP/FTPS connections"))
    secure = models.BooleanField(default=False, verbose_name=_("Secure (FTPS)"),
                                 help_text=_("Use TLS encryption for FTP. Only applies to FTP connections"))
    
    # SFTP specific settings
    private_key_file = models.CharField(max_length=500, blank=True, verbose_name=_("Private Key File Path"),
                                        help_text=_(
                                            "Path to SSH private key file (for SFTP). Optional if using password"))
    host_key_policy = models.CharField(
        max_length=10,
        choices=HostKeyPolicy.choices,
        default=HostKeyPolicy.AUTO,
        verbose_name=_("Host Key Policy"),
        help_text=_("How to handle unknown SSH host keys (SFTP only)")
    )
    look_for_keys = models.BooleanField(
        default=False,
        verbose_name=_("Look for SSH Keys"),
        help_text=_("Automatically search for SSH keys in default locations (~/.ssh/). SFTP only")
    )
    allow_agent = models.BooleanField(
        default=False,
        verbose_name=_("Allow SSH Agent"),
        help_text=_("Use SSH agent for authentication if available. SFTP only")
    )
    
    # Common settings
    timeout = models.IntegerField(default=20, verbose_name=_("Connection Timeout (seconds)"))
    decoder = models.CharField(max_length=255, choices=get_ftp_decoder_choices, verbose_name=_("Decoder"))
    
    csv_config = models.ForeignKey(
        StandardCSVConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("CSV Configuration"),
        help_text=_("Configuration for standard CSV files")
    )
    
    panels = NetworkConnection.panels + [
        FieldPanel("connection_type", widget=ConnectionTypeRadioSelect),
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("username"),
            FieldPanel("password"),
            FieldPanel("timeout"),
        ], heading=_("Basic Connection")),
        MultiFieldPanel([
            FieldPanel("passive_mode"),
            FieldPanel("secure"),
        ], heading=_("FTP/FTPS Settings")),
        MultiFieldPanel([
            FieldPanel("private_key_file"),
            FieldPanel("host_key_policy"),
            FieldPanel("look_for_keys"),
            FieldPanel("allow_agent"),
        ], heading=_("SFTP Settings")),
        FieldPanel("decoder", widget=FTPDecoderSelectWidget),
        FieldPanel("csv_config"),
        InlinePanel("variable_mappings", label=_("Variable Mapping"), heading=_("Variable Mappings")),
    ]
    
    class Meta:
        verbose_name = _("Network FTP/SFTP")
        verbose_name_plural = _("Network FTP/SFTPs")
    
    def get_decoder(self):
        from adl_ftp_plugin.registries import ftp_decoder_registry
        return ftp_decoder_registry.get(self.decoder)
    
    def clean(self):
        """Validate connection settings based on connection type"""
        super().clean()
        
        if self.connection_type == ConnectionType.SFTP:
            if not self.password and not self.private_key_file:
                raise ValidationError({
                    'password': _("Password or private key file is required for SFTP connections")
                })
        else:  # FTP/FTPS
            if not self.password:
                raise ValidationError({
                    'password': _("Password is required for FTP/FTPS connections")
                })
        
        # Validate decoder-specific requirements
        if self.decoder == "standard_csv":
            if not self.csv_config:
                raise ValidationError({
                    'csv_config': _("CSV Configuration is required when using 'Standard CSV' decoder")
                })
    
    def get_extra_model_admin_links(self):
        from .viewsets import StandardCSVConfigViewSet, TestDecoderConfigViewSet
        url = TestDecoderConfigViewSet().menu_url
        url = f"{url}?connection_id={self.id}"
        
        columns = [
            {
                "label": _("Test Decoder Configuration"),
                "url": url,
                "icon_name": "glasses",
                "kwargs": {"attrs": {"target": "_blank"}}
            }
        ]
        
        if self.decoder == "standard_csv" and self.csv_config:
            url = reverse(StandardCSVConfigViewSet().get_url_name("edit"), args=[self.csv_config.id])
            columns.append({
                "label": _("CSV Config"),
                "url": url,
                "icon_name": "doc-full",
                "kwargs": {"attrs": {"target": "_blank"}}
            })
        
        return columns
    
    @property
    def default_port(self):
        """Get default port based on connection type"""
        defaults = {
            ConnectionType.FTP: 21,
            ConnectionType.FTPS: 21,
            ConnectionType.SFTP: 22,
        }
        return defaults.get(self.connection_type, 21)
    
    @property
    def effective_port(self):
        """Get the port to use (specified or default)"""
        return self.port if self.port else self.default_port
    
    @property
    def ftp_connection_details(self):
        """Get connection details for FTP/FTPS clients"""
        return {
            "host": self.host,
            "port": self.effective_port,
            "user": self.username,
            "password": self.password,
            "passive": self.passive_mode,
            "secure": self.secure and self.connection_type == ConnectionType.FTPS,
            "timeout": self.timeout,
        }
    
    @property
    def sftp_connection_details(self):
        """Get connection details for SFTP clients"""
        details = {
            "host": self.host,
            "port": self.effective_port,
            "user": self.username,
            "timeout": self.timeout,
            "host_key_policy": self.host_key_policy,
            "look_for_keys": self.look_for_keys,
            "allow_agent": self.allow_agent,
        }
        
        if self.password:
            details["password"] = self.password
        if self.private_key_file:
            details["private_key"] = self.private_key_file
        
        return details
    
    def get_client(self):
        """Get appropriate client based on connection type"""
        if self.connection_type == ConnectionType.SFTP:
            return SFTPClient(**self.sftp_connection_details)
        else:
            return FTPClient(**self.ftp_connection_details)


class FTPVariableMapping(Orderable):
    network_ftp = ParentalKey(NetworkFTP, on_delete=models.CASCADE, related_name="variable_mappings")
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Parameter"))
    file_variable_name = models.CharField(max_length=255, verbose_name=_("File Variable Name"))
    file_variable_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, verbose_name=_("File Variable Unit"))
    
    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("file_variable_name"),
        FieldPanel("file_variable_unit"),
    ]
    
    @property
    def source_parameter_name(self):
        """Returns the shortcode of the variable."""
        return self.file_variable_name
    
    @property
    def source_parameter_unit(self):
        """Returns the unit of the variable."""
        return self.file_variable_unit


class FTPStationLink(StationLink):
    extra_list_display = ["ftp_path", "file_pattern", "start_date"]
    
    DATE_GRANULARITY_CHOICES = [
        ("year", _("Year")),
        ("month", _("Month")),
        ("day", _("Day")),
        ("hour", _("Hour")),
    ]
    
    MONTH_FORMAT_CHOICES = [
        ("m", _("Month, 2 digits with leading zeros. '01' to '12'")),
        ("n", _("Month without leading zeros. '1' to '12'")),
        ("M", _("Month, textual, 3 letters. 'Jan'")),
        ("b", _("Month, textual, 3 letters, lowercase. 'jan'")),
        ("F", _("Month, textual, full. 'January'")),
        ("f", _("Month, textual, full, lowercase. 'january'")),
    ]
    
    FILENAME_DATE_FORMAT_CHOICES = [
        # Compact formats (no separators)
        ("YYYYMMDD", _("YYYYMMDD - e.g., 20250115")),
        ("YYYYMMDDHH", _("YYYYMMDDHH - e.g., 2025011514")),
        ("YYYYMMDDHHMM", _("YYYYMMDDHHMM - e.g., 202501151430")),
        ("YYYYMMDDHHMMSS", _("YYYYMMDDHHMMSS - e.g., 20250115143045")),
        ("YYMMDD", _("YYMMDD - e.g., 250115")),
        ("YYMMDDHHMM", _("YYMMDDHHMM - e.g., 2501151430")),
        ("DDMMYYYY", _("DDMMYYYY - e.g., 15012025")),
        ("MMDDYYYY", _("MMDDYYYY - e.g., 01152025")),
        ("DDMMYY", _("DDMMYY - e.g., 150125")),
        ("MMDDYY", _("MMDDYY - e.g., 011525")),
        
        # Dash separated
        ("YYYY-MM-DD", _("YYYY-MM-DD - e.g., 2025-01-15")),
        ("YYYY-MM-DD-HH", _("YYYY-MM-DD-HH - e.g., 2025-01-15-14")),
        ("YYYY-MM-DD-HHMM", _("YYYY-MM-DD-HHMM - e.g., 2025-01-15-1430")),
        ("YYYY-MM-DD-HHMMSS", _("YYYY-MM-DD-HHMMSS - e.g., 2025-01-15-143045")),
        ("DD-MM-YYYY", _("DD-MM-YYYY - e.g., 15-01-2025")),
        ("MM-DD-YYYY", _("MM-DD-YYYY - e.g., 01-15-2025")),
        ("YY-MM-DD", _("YY-MM-DD - e.g., 25-01-15")),
        
        # Underscore separated
        ("YYYY_MM_DD", _("YYYY_MM_DD - e.g., 2025_01_15")),
        ("YYYY_MM_DD_HH", _("YYYY_MM_DD_HH - e.g., 2025_01_15_14")),
        ("YYYY_MM_DD_HHMM", _("YYYY_MM_DD_HHMM - e.g., 2025_01_15_1430")),
        ("YYYY_MM_DD_HHMMSS", _("YYYY_MM_DD_HHMMSS - e.g., 2025_01_15_143045")),
        ("DD_MM_YYYY", _("DD_MM_YYYY - e.g., 15_01_2025")),
        ("MM_DD_YYYY", _("MM_DD_YYYY - e.g., 01_15_2025")),
        
        # Dot separated
        ("YYYY.MM.DD", _("YYYY.MM.DD - e.g., 2025.01.15")),
        ("DD.MM.YYYY", _("DD.MM.YYYY - e.g., 15.01.2025")),
        ("MM.DD.YYYY", _("MM.DD.YYYY - e.g., 01.15.2025")),
        
        # ISO 8601 / RFC 3339 variants
        ("YYYY-MM-DDTHH", _("YYYY-MM-DDTHH - e.g., 2025-01-15T14")),
        ("YYYY-MM-DDTHHMMSS", _("YYYY-MM-DDTHHMMSS - e.g., 2025-01-15T143045")),
        ("YYYY-MM-DDTHH:MM:SS", _("YYYY-MM-DDTHH:MM:SS - e.g., 2025-01-15T14:30:45")),
        
        # Julian date formats
        ("YYYYDDD", _("YYYYDDD - e.g., 2025015 (Julian day)")),
        ("YYDDD", _("YYDDD - e.g., 25015 (Julian day)")),
        
        # Year and month only
        ("YYYYMM", _("YYYYMM - e.g., 202501")),
        ("YYYY-MM", _("YYYY-MM - e.g., 2025-01")),
        ("YYYY_MM", _("YYYY_MM - e.g., 2025_01")),
        
        # Text month formats
        ("YYYY-MMM-DD", _("YYYY-MMM-DD - e.g., 2025-Jan-15")),
        ("DD-MMM-YYYY", _("DD-MMM-YYYY - e.g., 15-Jan-2025")),
        ("YYYYMMMDD", _("YYYYMMMDD - e.g., 2025Jan15")),
        ("DDMMMYYYY", _("DDMMMYYYY - e.g., 15Jan2025")),
        
        # Unix timestamp
        ("TIMESTAMP", _("Unix Timestamp - e.g., 1705329600")),
    ]
    
    ftp_path = models.CharField(max_length=255, verbose_name=_("Remote Path"),
                                help_text=_("Path to the directory containing the data files"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
    
    # Directory structure by date
    dir_structured_by_date = models.BooleanField(default=False, verbose_name=_("Directory Structured by Date ?"),
                                                 help_text=_("Check if the files are structured by a combination of"
                                                             " year, month, day or hour in the remote path. Folders "
                                                             "structure expected to be in the format "
                                                             "[YYYY]/[MM]/[DD]/[HH]"))
    date_granularity = models.CharField(max_length=255, blank=True, null=True, choices=DATE_GRANULARITY_CHOICES,
                                        verbose_name=_("Date Granularity"),
                                        help_text=_("How far down the date hierarchy is the file located ? "
                                                    "This will be used to construct the final name of the folder in the remote path"))
    month_dir_format = models.CharField(max_length=255, blank=True, null=True, choices=MONTH_FORMAT_CHOICES,
                                        default="m", verbose_name=_("Month directory Format"), )
    
    # Filename date filtering
    filter_files_by_date = models.BooleanField(
        default=False,
        verbose_name=_("Filter files by date in filename"),
        help_text=_("Check if filenames contain dates that should be used to filter which files to download")
    )
    filename_date_format = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=FILENAME_DATE_FORMAT_CHOICES,
        verbose_name=_("Filename Date Format"),
        help_text=_("Format of the date in the filename (appears before file extension)")
    )
    
    start_date = models.DateTimeField(blank=True, null=True, validators=[validate_start_date],
                                      verbose_name=_("Start Date"),
                                      help_text=_("Start date for data pulling. Select a past date to include the "
                                                  "historical data. Leave blank for collecting realtime data only"), )
    skip_already_downloaded_files = models.BooleanField(default=True,
                                                        verbose_name=_("Skip downloading already downloaded files"),
                                                        help_text=_(
                                                            "Do not download files that have already been downloaded"))
    skip_already_processed_files = models.BooleanField(default=True,
                                                       verbose_name=_("Skip processing already processed files"),
                                                       help_text=_(
                                                           "Do not process files that have already been processed"))
    
    panels = StationLink.panels + [
        MultiFieldPanel([
            FieldPanel("ftp_path", widget=FTPDirectoryTreeSelectWidget()),
            FieldPanel("file_pattern"),
        ], heading=_("Remote Configuration")),
        MultiFieldPanel([
            FieldPanel("dir_structured_by_date"),
            FieldPanel("date_granularity"),
            FieldPanel("month_dir_format"),
        ], heading=_("Directory Structure")),
        MultiFieldPanel([
            FieldPanel("filter_files_by_date"),
            FieldPanel("filename_date_format"),
        ], heading=_("Filename Date Filtering")),
        MultiFieldPanel([
            FieldPanel("start_date"),
            FieldPanel("skip_already_downloaded_files"),
        ], heading=_("Data Collection")),
        InlinePanel("variable_mappings", label=_("Variable Mapping"), heading=_("Variable Mappings"),
                    help_text=_(
                        "Set the station specific variable mapping for the data files, if the general variable mapping in Connection does not apply for this station  ")),
    ] + StationLink.aggregation_panels
    
    class Meta:
        verbose_name = _("FTP/SFTP Station Link")
        verbose_name_plural = _("FTP/SFTP Station Links")
    
    def __str__(self):
        return f"{self.network_connection} - {self.station.wigos_id} - {self.station}"
    
    def clean(self):
        """Validate configuration"""
        super().clean()
        
        if self.filter_files_by_date and not self.filename_date_format:
            raise ValidationError({
                'filename_date_format': _("Filename date format is required when filtering files by date")
            })
    
    def get_variable_mappings(self):
        """Returns the variable mappings for this station link."""
        connection_variable_mappings = self.network_connection.variable_mappings.all() or []
        station_variable_mappings = self.variable_mappings.all() or []
        
        resolved = {m.adl_parameter_id: m for m in connection_variable_mappings}
        resolved.update({m.adl_parameter_id: m for m in station_variable_mappings})
        
        return list(resolved.values())
    
    def get_first_collection_date(self):
        """Returns the first collection date for this station link."""
        return self.start_date


class FTPStationLinkVariableMapping(Orderable):
    station_link = ParentalKey(FTPStationLink, on_delete=models.CASCADE, related_name="variable_mappings")
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Parameter"))
    file_variable_name = models.CharField(max_length=255, verbose_name=_("File Variable Name"))
    file_variable_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, verbose_name=_("File Variable Unit"))
    
    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("file_variable_name"),
        FieldPanel("file_variable_unit"),
    ]
    
    @property
    def source_parameter_name(self):
        """Returns the shortcode of the variable."""
        return self.file_variable_name
    
    @property
    def source_parameter_unit(self):
        """Returns the unit of the variable."""
        return self.file_variable_unit


def get_ftp_data_file_upload_path(instance, filename):
    return f"ftp_data_files/{instance.station_link.network_connection.network.id}/{instance.station_link.station.id}/{filename}"


class FTPStationDataFile(models.Model):
    station_link = models.ForeignKey(FTPStationLink, on_delete=models.CASCADE, related_name="data_files")
    file_name = models.CharField(max_length=255, verbose_name=_("File Name"))
    file = models.FileField(upload_to=get_ftp_data_file_upload_path, verbose_name=_("File"))
    processed = models.BooleanField(default=False, verbose_name=_("Processed"))
    variable_mappings = models.ManyToManyField(FTPVariableMapping, verbose_name=_("Variable Mappings"))
    
    class Meta:
        verbose_name = _("Remote Station Data File")
        verbose_name_plural = _("Remote Station Data Files")
        indexes = [
            models.Index(
                fields=["station_link", "file_name", "id"],
                name="idx_ftpfile_station_file_id",
            ),
        ]
    
    def __str__(self):
        return f"{self.station_link} - {self.file_name}"


class BaseFTPUpload(models.Model):
    """Base class for upload configurations supporting both FTP and SFTP"""
    WRITE_MODES = (
        ("append", _("Append record to single daily file")),
        ("new_file", _("Create a new file for each record")),
    )
    
    connection_type = models.CharField(
        max_length=10,
        choices=ConnectionType.choices,
        default=ConnectionType.FTP,
        verbose_name=_("Connection Type")
    )
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.CharField(max_length=255, blank=True, verbose_name=_("Port"),
                            help_text=_("Leave blank for default (21 for FTP/FTPS, 22 for SFTP)"))
    user = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"), blank=True)
    
    # FTP specific
    passive = models.BooleanField(default=True, verbose_name=_("Use FTP Passive Mode"))
    secure = models.BooleanField(default=False, verbose_name=_("Secure (FTPS)"))
    
    # SFTP specific
    private_key_file = models.CharField(max_length=500, blank=True, verbose_name=_("Private Key File"))
    host_key_policy = models.CharField(
        max_length=10,
        choices=HostKeyPolicy.choices,
        default=HostKeyPolicy.AUTO,
        verbose_name=_("Host Key Policy")
    )
    look_for_keys = models.BooleanField(default=False, verbose_name=_("Look for SSH Keys"))
    allow_agent = models.BooleanField(default=False, verbose_name=_("Allow SSH Agent"))
    
    directory = models.CharField(max_length=255, verbose_name=_("Remote Directory"),
                                 help_text=_("Directory on the server to upload files to"))
    timezone = TimeZoneField(default='UTC', verbose_name=_("Timezone for output dates"),
                             help_text=_("Timezone to use for file dates. UTC highly recommended"))
    
    write_mode = models.CharField(max_length=20, choices=WRITE_MODES, default="append",
                                  verbose_name=_("Write Mode"))
    
    class Meta:
        abstract = True
    
    def clean(self):
        """Validate connection settings"""
        super().clean()
        
        if self.connection_type == ConnectionType.SFTP:
            if not self.password and not self.private_key_file:
                raise ValidationError({
                    'password': _("Password or private key file is required for SFTP connections")
                })
        else:  # FTP/FTPS
            if not self.password:
                raise ValidationError({
                    'password': _("Password is required for FTP/FTPS connections")
                })
    
    @property
    def effective_port(self):
        """Get the port to use"""
        if self.port:
            return int(self.port)
        
        defaults = {
            ConnectionType.FTP: 21,
            ConnectionType.FTPS: 21,
            ConnectionType.SFTP: 22,
        }
        return defaults.get(self.connection_type, 21)
    
    @property
    def connection_details(self):
        """Get connection details for the appropriate client"""
        if self.connection_type == ConnectionType.SFTP:
            details = {
                "host": self.host,
                "port": self.effective_port,
                "user": self.user,
                "host_key_policy": self.host_key_policy,
                "look_for_keys": self.look_for_keys,
                "allow_agent": self.allow_agent,
            }
            if self.password:
                details["password"] = self.password
            if self.private_key_file:
                details["private_key"] = self.private_key_file
            return details
        else:
            return {
                "host": self.host,
                "port": self.effective_port,
                "user": self.user,
                "password": self.password,
                "passive": self.passive,
                "secure": self.secure and self.connection_type == ConnectionType.FTPS,
            }
    
    def get_client(self):
        """Get appropriate client based on connection type"""
        if self.connection_type == ConnectionType.SFTP:
            return SFTPClient(**self.connection_details)
        else:
            return FTPClient(**self.connection_details)


class FTPUpload(BaseFTPUpload, DispatchChannel):
    panels = DispatchChannel.base_panels + [
        FieldPanel("timezone"),
        FieldPanel("connection_type"),
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("user"),
            FieldPanel("password"),
            FieldPanel("directory"),
        ], heading=_("Basic Configuration")),
        MultiFieldPanel([
            FieldPanel("passive"),
            FieldPanel("secure"),
        ], heading=_("FTP/FTPS Settings")),
        MultiFieldPanel([
            FieldPanel("private_key_file"),
            FieldPanel("host_key_policy"),
            FieldPanel("look_for_keys"),
            FieldPanel("allow_agent"),
        ], heading=_("SFTP Settings")),
        FieldPanel("write_mode"),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("Standard FTP/SFTP Upload")
        verbose_name_plural = _("Standard FTP/SFTP Uploads")
    
    def send_station_data(self, station_link, station_data_records):
        return dispatch_to_ftp(self, station_data_records)


class SmartMetFTPUpload(BaseFTPUpload, DispatchChannel):
    update_stations_metadata_after_station_update = models.BooleanField(
        default=True, verbose_name=_("Update stations metadata on save"),
        help_text=_("If checked, the stations metadata CSV file will be uploaded to the FTP/SFTP "
                    "server on saving this configuration"))
    stations_csv_ftp_path = models.CharField(max_length=255, blank=True, null=True,
                                             verbose_name=_("Stations Metadata CSV FTP Path"),
                                             help_text=_(
                                                 "Path on the FTP/SFTP server to upload the stations metadata CSV file"))
    stations_file_name = models.CharField(max_length=255, default="stations.csv",
                                          blank=True,
                                          null=True,
                                          verbose_name=_("Stations Metadata CSV File Name"),
                                          help_text=_("Name of the stations metadata CSV file to upload"))
    
    panels = DispatchChannel.base_panels + [
        FieldPanel("connection_type"),
        FieldPanel("timezone"),
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("user"),
            FieldPanel("password"),
            FieldPanel("directory"),
            FieldPanel("write_mode"),
        ], heading=_("Basic Configuration")),
        
        MultiFieldPanel([
            FieldPanel("update_stations_metadata_after_station_update"),
            FieldPanel("stations_csv_ftp_path"),
            FieldPanel("stations_file_name"),
        ], heading=_("Stations Metadata CSV Configuration")),
        
        MultiFieldPanel([
            FieldPanel("passive"),
            FieldPanel("secure"),
        ], heading=_("FTP/FTPS Settings")),
        MultiFieldPanel([
            FieldPanel("private_key_file"),
            FieldPanel("host_key_policy"),
        ], heading=_("SFTP Settings")),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("SmartMet FTP/SFTP Upload")
        verbose_name_plural = _("SmartMet FTP/SFTP Uploads")
    
    @property
    def smartmet_params(self):
        SMARTMET_PARAMETER_NAMES = getattr(settings, "SMARTMET_PARAMETER_NAMES", [])
        return SMARTMET_PARAMETER_NAMES
    
    def clean_parameter_mapping(self, parameter_mapping):
        if parameter_mapping.channel_parameter not in self.smartmet_params:
            message = "The provide channel parameter is not in the known list of parameters"
            raise ValidationError(message)
    
    def get_parameter_mapping_values(self):
        return self.smartmet_params
    
    def send_station_data(self, station_link, station_data_records):
        return dispatch_to_ftp(self, station_data_records, create_station_dir=False, include_wigos_id=False,
                               use_single_timestamp=True, include_header=False)
    
    def get_smartmet_metadata_csv(self):
        metadata_csv = get_station_metadata_csv(self)
        return metadata_csv
    
    def upload_stations_metadata(self):
        if not self.update_stations_metadata_after_station_update:
            logger.info(
                f"[SMARTMET METADATA] Skipping stations metadata upload as it is disabled for channel: {self.name}")
            return
        
        if not self.stations_csv_ftp_path:
            logger.warning(
                f"[SMARTMET METADATA] No FTP path configured for stations metadata CSV in channel: {self.name}")
            return
        
        csv_file = self.get_smartmet_metadata_csv()
        
        # Use the flexible client
        client = self.get_client()
        
        directory = self.stations_csv_ftp_path
        file_name = self.stations_file_name
        if not file_name:
            file_name = "stations.csv"
        remote_path = f"{directory}/{file_name}"
        
        logger.info(f"[SMARTMET METADATA] Uploading file to '{remote_path}' via {self.connection_type}")
        client.put(csv_file, remote_path)
        client.close()
    
    def after_update_station_links(self):
        self.upload_stations_metadata()


@receiver(post_save, sender=SmartMetFTPUpload)
def upload_smartmet_metadata_on_save(sender, instance, created, **kwargs):
    """
    Signal handler that uploads station metadata CSV after SmartMetFTPUpload is saved.
    """
    try:
        # Only upload if the instance is enabled
        if not instance.enabled:
            logger.debug(f"[SMARTMET METADATA] Skipping metadata upload for disabled channel: {instance.name}")
            return
        
        # Check if connection details are complete
        if not all([instance.host, instance.user, instance.directory]):
            logger.warning(f"[SMARTMET METADATA] Incomplete connection configuration for channel: {instance.name}")
            return
        
        logger.info(
            f"[SMARTMET METADATA] Starting metadata upload for channel: {instance.name} via {instance.connection_type}")
        instance.upload_stations_metadata()
        logger.info(f"[SMARTMET METADATA] Successfully uploaded metadata for channel: {instance.name}")
    
    except Exception as e:
        logger.error(f"[SMARTMET METADATA] Failed to upload metadata for channel {instance.name}: {str(e)}")
