import logging
from enum import Enum

from adl.core.models import DataParameter, Unit
from adl.core.models import NetworkConnection, StationLink, DispatchChannel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
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
from adl_ftp_plugin.widgets import FTPDirectoryTreeSelectWidget
from .smartmet_utils import get_station_metadata_csv

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
    
    panels = NetworkConnection.panels + [
        FieldPanel("connection_type"),
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
        FieldPanel("decoder"),
        InlinePanel("variable_mappings", label=_("Variable Mapping"), heading=_("Variable Mappings")),
    ]
    
    class Meta:
        verbose_name = _("Network FTP/SFTP")
        verbose_name_plural = _("Network FTP/SFTPs")
    
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
    
    ftp_path = models.CharField(max_length=255, verbose_name=_("Remote Path"),
                                help_text=_("Path to the directory containing the data files"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
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
        ], heading=_("File Structure")),
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
        FieldPanel("connection_type"),
        FieldPanel("timezone"),
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
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("Standard FTP/SFTP Upload")
        verbose_name_plural = _("Standard FTP/SFTP Uploads")
    
    def send_station_data(self, station_link, station_data_records):
        return dispatch_to_ftp(self, station_data_records)


class SmartMetFTPUpload(BaseFTPUpload, DispatchChannel):
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
        csv_file = self.get_smartmet_metadata_csv()
        
        # Use the flexible client
        client = self.get_client()
        
        directory = self.directory
        remote_path = f"{directory}/stations.csv"
        
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
