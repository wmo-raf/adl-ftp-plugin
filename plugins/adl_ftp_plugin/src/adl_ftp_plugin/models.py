import logging

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
from adl_ftp_plugin.utils import get_ftp_decoder_choices
from adl_ftp_plugin.validators import validate_start_date
from adl_ftp_plugin.widgets import FTPDirectoryTreeSelectWidget
from .smartmet_utils import get_station_metadata_csv

logger = logging.getLogger(__name__)


class NetworkFTP(NetworkConnection):
    station_link_model_string_label = "adl_ftp_plugin.FTPStationLink"
    
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.IntegerField(verbose_name=_("Port"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"))
    decoder = models.CharField(max_length=255, choices=get_ftp_decoder_choices, verbose_name=_("Decoder"))
    passive_mode = models.BooleanField(default=True, verbose_name=_("Use FTP Passive mode"))
    secure = models.BooleanField(default=False, verbose_name=_("Secure"))
    
    panels = NetworkConnection.panels + [
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("username"),
            FieldPanel("password"),
            FieldPanel("passive_mode"),
            FieldPanel("secure"),
        ], heading=_("FTP Credentials")),
        FieldPanel("decoder"),
        InlinePanel("variable_mappings", label=_("Variable Mapping"), heading=_("Variable Mappings")),
    ]
    
    class Meta:
        verbose_name = _("Network FTP")
        verbose_name_plural = _("Network FTPs")
    
    @property
    def ftp_connection_details(self):
        return {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "password": self.password,
            "passive": self.passive_mode,
            "secure": self.secure,
        }
    
    def get_ftp_client(self):
        from .ftp import FTPClient
        connection_details = self.ftp_connection_details
        ftp_client = FTPClient(**connection_details)
        return ftp_client


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
        """
        Returns the shortcode of the TAHMO variable.
        """
        return self.file_variable_name
    
    @property
    def source_parameter_unit(self):
        """
        Returns the unit of the TAHMO variable.
        """
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
    
    ftp_path = models.CharField(max_length=255, verbose_name=_("FTP Path"),
                                help_text=_("Path to the directory containing the data files"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
    dir_structured_by_date = models.BooleanField(default=False, verbose_name=_("Directory Structured by Date ?"),
                                                 help_text=_("Check if the files are structured by a combination of"
                                                             " year, month, day or hour in the FTP path. Folders "
                                                             "structure expected to be in the format "
                                                             "[YYYY]/[MM]/[DD]/[HH]"))
    date_granularity = models.CharField(max_length=255, blank=True, null=True, choices=DATE_GRANULARITY_CHOICES,
                                        verbose_name=_("Date Granularity"),
                                        help_text=_("How far down the date hierarchy is the file located ? "
                                                    "This will be used to construct the final name of the folder in the FTP path"))
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
        ], heading=_("FTP Configuration")),
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
        verbose_name = _("FTP Station Link")
        verbose_name_plural = _("FTP Station Links")
    
    def __str__(self):
        return f"{self.network_connection} - {self.station.wigos_id} - {self.station}"
    
    def get_variable_mappings(self):
        """
        Returns the variable mappings for this station link.
        """
        
        connection_variable_mappings = self.network_connection.variable_mappings.all() or []
        station_variable_mappings = self.variable_mappings.all() or []
        
        resolved = {m.adl_parameter_id: m for m in connection_variable_mappings}
        resolved.update({m.adl_parameter_id: m for m in station_variable_mappings})
        
        return list(resolved.values())
    
    def get_first_collection_date(self):
        """
        Returns the first collection date for this station link.
        Returns None if no start date is set.
        """
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
        """
        Returns the shortcode of the TAHMO variable.
        """
        return self.file_variable_name
    
    @property
    def source_parameter_unit(self):
        """
        Returns the unit of the TAHMO variable.
        """
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
        verbose_name = _("FTP Station Data File")
        verbose_name_plural = _("FTP Station Data Files")
    
    def __str__(self):
        return f"{self.station_link} - {self.file_name}"


class BaseFTPUpload(models.Model):
    WRITE_MODES = (
        ("append", _("Append record to single daily file")),
        ("new_file", _("Create a new file for each record")),
    )
    host = models.CharField(max_length=255, verbose_name=_("FTP Host"))
    port = models.CharField(max_length=255, verbose_name=_("FTP Port"))
    user = models.CharField(max_length=255, verbose_name=_("FTP User"))
    password = models.CharField(max_length=255, verbose_name=_("FTP Password"))
    passive = models.BooleanField(default=False, verbose_name=_("Use FTP Passive Mode"))
    directory = models.CharField(max_length=255, verbose_name=_("FTP Directory"),
                                 help_text=_("Directory on the FTP server to upload files to"))
    timezone = TimeZoneField(default='UTC', verbose_name=_("Timezone to use for date/time"),
                             help_text=_("Timezone used by the station for recording observations"))
    
    write_mode = models.CharField(max_length=20, choices=WRITE_MODES, default="append",
                                  verbose_name=_("FTP Write Mode"))
    
    class Meta:
        abstract = True
    
    @property
    def connection_details(self):
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "passive": self.passive,
        }


class FTPUpload(BaseFTPUpload, DispatchChannel):
    panels = DispatchChannel.base_panels + [
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("user"),
            FieldPanel("password"),
            FieldPanel("passive"),
            FieldPanel("directory"),
            FieldPanel("passive"),
        ], heading=_("FTP Configuration")),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("Standard FTP Upload")
        verbose_name_plural = _("Standard FTP Uploads")
    
    def send_station_data(self, station_link, station_data_records):
        return dispatch_to_ftp(self, station_data_records)


class SmartMetFTPUpload(BaseFTPUpload, DispatchChannel):
    panels = DispatchChannel.base_panels + [
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("user"),
            FieldPanel("password"),
            FieldPanel("passive"),
            FieldPanel("directory"),
            FieldPanel("write_mode"),
        ], heading=_("FTP Configuration")),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("SmartMet FTP Upload")
        verbose_name_plural = _("SmartMet FTP Uploads")
    
    @property
    def smartmet_params(self):
        SMARTMET_PARAMETER_NAMES = getattr(settings, "SMARTMET_PARAMETER_NAMES", [])
        return SMARTMET_PARAMETER_NAMES
    
    def clean_parameter_mapping(self, parameter_mapping):
        if parameter_mapping.channel_parameter not in self.smartmet_params:
            message = "The provide channel parameter is not in the known list of parameters"
            raise ValidationError(message)
    
    def get_parameter_mapping_values(self):
        return smartmet_params
    
    def send_station_data(self, station_link, station_data_records):
        return dispatch_to_ftp(self, station_data_records, create_station_dir=False, include_wigos_id=False,
                               use_single_timestamp=True, include_header=False)
    
    def get_smartmet_metadata_csv(self):
        metadata_csv = get_station_metadata_csv(self)
        return metadata_csv
    
    def upload_stations_metadata(self):
        csv_file = self.get_smartmet_metadata_csv()
        
        ftp_client = FTPClient(**self.connection_details)
        
        directory = self.directory
        
        remote_path = f"{directory}/stations.csv"
        
        logger.info(f"[SMARTMET METADATA] Uploading file to '{remote_path}'")
        ftp_client.put(csv_file, remote_path)
        
        ftp_client.close()
    
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
        
        # Check if FTP connection details are complete
        if not all([instance.host, instance.user, instance.directory]):
            logger.warning(f"[SMARTMET METADATA] Incomplete FTP configuration for channel: {instance.name}")
            return
        
        logger.info(f"[SMARTMET METADATA] Starting metadata upload for channel: {instance.name}")
        instance.upload_stations_metadata()
        logger.info(f"[SMARTMET METADATA] Successfully uploaded metadata for channel: {instance.name}")
    
    except Exception as e:
        logger.error(f"[SMARTMET METADATA] Failed to upload metadata for channel {instance.name}: {str(e)}")
