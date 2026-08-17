import fnmatch
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
from adl_ftp_plugin.ftp import FTPClient, FTPError
from adl_ftp_plugin.ftp.sftp import SFTPClient, SFTPError
from adl_ftp_plugin.utils import (
    get_ftp_decoder_choices,
    get_date_paths,
    get_dates_to_now,
    normalize_path,
)
from adl_ftp_plugin.validators import validate_start_date
from .date_formats import FILENAME_DATE_FORMAT_CHOICES
from .smartmet_utils import get_station_metadata_csv
from .widgets import (
    ConnectionTypeRadioSelect,
    ConditionalRadioSelect,
    FTPDirectoryTreeSelectWidget,
    FTPDecoderSelectWidget
)

logger = logging.getLogger(__name__)

# FTPError / SFTPError status codes -> the flat failure vocabulary shared
# with core (adl.core.classification.FAILURE_CATEGORIES). 502 is absent on
# purpose: it collapses DNS, refused and TLS faults into one code, and
# core's own DNS/TCP probe steps already name those faults — declining a
# category beats guessing one.
SOURCE_CHECK_ERROR_CATEGORIES = {
    401: "AUTH_FAILED",
    403: "PERMISSION_DENIED",
    404: "PATH_NOT_FOUND",
    504: "TCP_TIMEOUT",
}


def failed_source_check_result(error):
    """A FAILED SourceCheckResult for an FTPError / SFTPError, with the
    category derived from the error's status code where unambiguous."""
    from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
    return SourceCheckResult(
        status=SourceCheckStatus.FAILED,
        category=SOURCE_CHECK_ERROR_CATEGORIES.get(error.status),
        message=str(error.message),
    )


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
    
    no_data_value = models.FloatField(blank=True, null=True, verbose_name=_("No Data Value"),
                                      help_text=_("Value used in the data, to indicate a missing observation"))
    
    panels = [
        FieldPanel("name"),
        MultiFieldPanel([
            FieldPanel("has_header"),
            FieldPanel("delimiter"),
            FieldPanel("skip_rows"),
            FieldPanel("no_data_value"),
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
        
        # Offered only when the decoder declares its variables; swallows an
        # unregistered/uninstalled decoder so the connections list never breaks.
        from .decoder_variables import get_decoder_variables
        if get_decoder_variables(self):
            columns.append({
                "label": _("Populate Variable Mappings from Decoder"),
                "url": reverse("populate_variable_mappings_from_decoder", args=[self.id]),
                "icon_name": "plus-inverse",
                "kwargs": {"attrs": {}}
            })
        
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

    def get_source_endpoint(self):
        """The (host, port) core's generic DNS -> TCP probe dials (layer 4
        of the ingestion diagnostic)."""
        return self.host, self.effective_port

    def check_source(self):
        """
        Connect and authenticate against the server, read-only, and report
        whether the source accepts our credentials (layer 5 of the ingestion
        diagnostic). Nothing is listed, fetched or written.
        """
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
        from django.utils.translation import gettext
        try:
            client = self.get_client()
        except (FTPError, SFTPError) as e:
            return failed_source_check_result(e)
        client.close()
        return SourceCheckResult(
            status=SourceCheckStatus.OK,
            message=gettext("Connected and authenticated to %(host)s:%(port)s as %(user)s.") % {
                "host": self.host,
                "port": self.effective_port,
                "user": self.username,
            },
        )


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


class FTPListingStrategy(models.TextChoices):
    PATTERN_ONLY = "pattern_only", _("Pattern Only — no date filtering")
    FILTER_BY_DATE = "filter_by_date", _("Filter by Date — list all files, filter by date in filename")
    DIRECT_FETCH = "direct_fetch", _("Direct Fetch — construct filenames from time increment, no listing")


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
    
    # -------------------------------------------------------------------------
    # Remote Configuration — shared by all strategies
    # -------------------------------------------------------------------------
    ftp_path = models.CharField(max_length=255, verbose_name=_("Remote Path"),
                                help_text=_("Path to the directory containing the data files"))
    file_pattern = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_("File Pattern"),
        help_text=_("Glob pattern to match files e.g. 'Station1_*.dat'. Not required for Direct Fetch.")
    )
    
    # -------------------------------------------------------------------------
    # Directory Structure — shared by all strategies, about file organization
    # -------------------------------------------------------------------------
    dir_structured_by_date = models.BooleanField(
        default=False, verbose_name=_("Directory Structured by Date ?"),
        help_text=_("Check if the files are structured by a combination of"
                    " year, month, day or hour in the remote path. Folders "
                    "structure expected to be in the format "
                    "[YYYY]/[MM]/[DD]/[HH]"))
    date_granularity = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        choices=DATE_GRANULARITY_CHOICES,
        verbose_name=_("Date Granularity"),
        help_text=_("How far down the date hierarchy is the file located ? "
                    "This will be used to construct the final name of the folder in the remote path"))
    month_dir_format = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        choices=MONTH_FORMAT_CHOICES,
        default="m", verbose_name=_("Month directory Format"),
    )
    
    # -------------------------------------------------------------------------
    # Listing Strategy — drives which fields below are used
    # -------------------------------------------------------------------------
    
    listing_strategy = models.CharField(
        max_length=40,
        choices=FTPListingStrategy.choices,
        default=FTPListingStrategy.PATTERN_ONLY,
        verbose_name=_("File Listing Strategy"),
        help_text=_(
            "How files are located on the server. "
            "'Pattern Only' for static filenames. "
            "'Filter by Date' for dated filenames requiring a full listing. "
            "'Direct Fetch' for predictable time-incremented filenames — skips listing entirely."
        )
    )
    
    # -------------------------------------------------------------------------
    # Filter by Date — only used when listing_strategy = filter_by_date
    # -------------------------------------------------------------------------
    filename_date_format = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=FILENAME_DATE_FORMAT_CHOICES,
        verbose_name=_("Filename Date Format"),
        help_text=_("Format of the date in the filename (appears before file extension)")
    )
    filename_date_timezone = TimeZoneField(
        default="UTC",
        verbose_name=_("Filename Date Timezone"),
        help_text=_("Timezone to use when parsing dates from filenames")
    )
    
    # -------------------------------------------------------------------------
    # Direct Fetch — only used when listing_strategy = direct_fetch
    # -------------------------------------------------------------------------
    direct_fetch_prefix = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_("File Prefix"),
        help_text=_(
            "Everything in the filename that appears before the datetime. "
            "e.g. for 'STATION_001_20260219122000.txt' the prefix is 'STATION_001_'. "
            "The datetime and extension are appended automatically."
        )
    )
    direct_fetch_interval_minutes = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_("File Interval (minutes)"),
        help_text=_("Interval in minutes between files e.g. 10 for files generated every 10 minutes.")
    )
    direct_fetch_datetime_format = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=FILENAME_DATE_FORMAT_CHOICES,
        verbose_name=_("File Datetime Format"),
        help_text=_(
            "Format of the datetime embedded in the filename. "
        )
    )
    direct_fetch_datetime_timezone = TimeZoneField(
        default="UTC",
        verbose_name=_("File Datetime Timezone"),
        help_text=_(
            "Timezone of the datetime embedded in the filename. "
            "Check with your data provider whether the filename datetime is local time or UTC."
        )
    )
    direct_fetch_file_extension = models.CharField(
        max_length=10,
        default=".txt",
        blank=True,
        null=True,
        verbose_name=_("File Extension"),
        help_text=_("File extension including the dot. e.g. '.txt', '.csv'")
    )
    
    # -------------------------------------------------------------------------
    # Data Collection — shared by all strategies
    # -------------------------------------------------------------------------
    start_date = models.DateTimeField(
        blank=True,
        null=True,
        validators=[validate_start_date],
        verbose_name=_("Start Date"),
        help_text=_("Start date for data pulling. Select a past date to include the "
                    "historical data. Leave blank for collecting realtime data only"),
    )
    skip_already_downloaded_files = models.BooleanField(
        default=True,
        verbose_name=_("Skip downloading already downloaded files"),
        help_text=_("Do not download files that have already been downloaded")
    )
    skip_already_processed_files = models.BooleanField(
        default=True,
        verbose_name=_("Skip processing already processed files"),
        help_text=_("Do not process files that have already been processed")
    )
    
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
            FieldPanel("listing_strategy"),
        ], heading=_("File Listing Strategy")),
        MultiFieldPanel([
            FieldPanel("filename_date_format"),
            FieldPanel("filename_date_timezone"),
        ], heading=_("Filter by Date")),
        MultiFieldPanel([
            FieldPanel("direct_fetch_prefix"),
            FieldPanel("direct_fetch_datetime_format"),
            FieldPanel("direct_fetch_interval_minutes"),
            FieldPanel("direct_fetch_datetime_timezone"),
            FieldPanel("direct_fetch_file_extension"),
        ], heading=_("Direct Fetch")),
        MultiFieldPanel([
            FieldPanel("start_date"),
            FieldPanel("skip_already_downloaded_files"),
        ], heading=_("Data Collection")),
        InlinePanel(
            "variable_mappings",
            label=_("Variable Mapping"),
            heading=_("Variable Mappings"),
            help_text=_(
                "Set the station specific variable mapping for the data files, "
                "if the general variable mapping in Connection does not apply for this station"
            )
        ),
    ] + StationLink.aggregation_panels
    
    class Meta:
        verbose_name = _("FTP/SFTP Station Link")
        verbose_name_plural = _("FTP/SFTP Station Links")
    
    def __str__(self):
        return f"{self.network_connection} - {self.station.wigos_id} - {self.station}"
    
    def clean(self):
        super().clean()
        
        if self.listing_strategy in [FTPListingStrategy.PATTERN_ONLY, FTPListingStrategy.FILTER_BY_DATE]:
            if not self.file_pattern:
                raise ValidationError({
                    'file_pattern': _(
                        "File pattern is required for Pattern Only and Filter by Date strategies"
                    )
                })
        
        if self.listing_strategy == FTPListingStrategy.FILTER_BY_DATE:
            if not self.filename_date_format:
                raise ValidationError({
                    'filename_date_format': _(
                        "Filename date format is required when using Filter by Date strategy"
                    )
                })
        
        if self.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
            if not self.direct_fetch_prefix:
                raise ValidationError({
                    'direct_fetch_prefix': _(
                        "File prefix is required when using Direct Fetch strategy"
                    )
                })
            if not self.direct_fetch_interval_minutes:
                raise ValidationError({
                    'direct_fetch_interval_minutes': _(
                        "File interval is required when using Direct Fetch strategy"
                    )
                })
            if not self.direct_fetch_datetime_format:
                raise ValidationError({
                    'direct_fetch_datetime_format': _(
                        "Datetime format is required when using Direct Fetch strategy"
                    )
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

    def resolve_source_path(self):
        """The remote directory this station's files are expected in right
        now — with the current date period appended when the directory tree
        is structured by date."""
        if self.dir_structured_by_date and self.date_granularity:
            dates = get_dates_to_now(
                date_granularity=self.date_granularity,
                timezone=self.timezone,
            )
            paths = get_date_paths(
                self.ftp_path, dates, self.date_granularity, self.month_dir_format
            )
            return normalize_path(paths[-1])
        return normalize_path(self.ftp_path)

    def source_file_pattern(self):
        """The glob this station's files are matched against, across all
        listing strategies."""
        if self.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
            prefix = self.direct_fetch_prefix or ""
            extension = self.direct_fetch_file_extension or ""
            return f"{prefix}*{extension}" if (prefix or extension) else "*"
        return self.file_pattern or "*"

    def check_station_source(self):
        """
        Resolve this station's remote path, list it read-only, and report
        the resolved path and the match count (layer 5 of the ingestion
        diagnostic, station-scoped). Zero matches is OK: a date-structured
        directory is legitimately empty at every rollover, and the operator
        judges the resolved path and count better than a rule can.
        """
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
        from django.utils.translation import gettext
        path = self.resolve_source_path()
        pattern = self.source_file_pattern()
        try:
            client = self.network_connection.get_client()
        except (FTPError, SFTPError) as e:
            return failed_source_check_result(e)
        try:
            if not client.cd(path):
                return SourceCheckResult(
                    status=SourceCheckStatus.FAILED,
                    category="PATH_NOT_FOUND",
                    message=gettext("Resolved remote path %(path)s was not found on the server.") % {
                        "path": path,
                    },
                )
            listing = client.list(path, extra=True)
            names = [entry["name"] for entry in listing]
            matched_count = len(fnmatch.filter(names, pattern))
            return SourceCheckResult(
                status=SourceCheckStatus.OK,
                message=gettext(
                    "Resolved remote path %(path)s: %(count)s file(s) matching '%(pattern)s'."
                ) % {"path": path, "count": matched_count, "pattern": pattern},
            )
        except (FTPError, SFTPError) as e:
            return failed_source_check_result(e)
        finally:
            client.close()


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
    wagtail_reference_index_ignore = True

    station_link = models.ForeignKey(FTPStationLink, on_delete=models.CASCADE, related_name="data_files")
    file_name = models.CharField(max_length=255, verbose_name=_("File Name"))
    file = models.FileField(upload_to=get_ftp_data_file_upload_path, verbose_name=_("File"))
    processed = models.BooleanField(default=False, verbose_name=_("Processed"))
    variable_mappings = models.ManyToManyField(FTPVariableMapping, verbose_name=_("Variable Mappings"))
    processed_at = models.DateTimeField(null=True, blank=True)
    
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
