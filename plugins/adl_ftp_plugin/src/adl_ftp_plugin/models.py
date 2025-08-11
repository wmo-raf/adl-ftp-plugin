from adl.core.models import DataParameter, Unit
from adl.core.models import NetworkConnection, StationLink, DispatchChannel
from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from timezone_field import TimeZoneField
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from adl_ftp_plugin.dispatchers.ftp import upload_to_ftp
from adl_ftp_plugin.utils import get_ftp_decoder_choices
from adl_ftp_plugin.validators import validate_start_date
from adl_ftp_plugin.widgets import FTPDirectoryTreeSelectWidget


class NetworkFTP(NetworkConnection):
    station_link_model_string_label = "adl_ftp_plugin.FTPStationLink"
    
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.IntegerField(verbose_name=_("Port"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"))
    decoder = models.CharField(max_length=255, choices=get_ftp_decoder_choices, verbose_name=_("Decoder"))
    
    panels = NetworkConnection.panels + [
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("username"),
            FieldPanel("password"),
        ], heading=_("FTP Credentials")),
        FieldPanel("decoder"),
        InlinePanel("variable_mappings", label=_("Variable Mapping"), heading=_("Variable Mappings")),
    ]
    
    class Meta:
        verbose_name = _("Network FTP")
        verbose_name_plural = _("Network FTPs")


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


class FTPUpload(DispatchChannel):
    WRITE_MODES = (
        ("append", _("Append record to single daily file")),
        ("new_file", _("Create a new file for each record")),
    )
    host = models.CharField(max_length=255, verbose_name=_("FTP Host"))
    port = models.CharField(max_length=255, verbose_name=_("FTP Port"))
    user = models.CharField(max_length=255, verbose_name=_("FTP User"))
    password = models.CharField(max_length=255, verbose_name=_("FTP Password"))
    directory = models.CharField(max_length=255, verbose_name=_("FTP Directory"),
                                 help_text=_("Directory on the FTP server to upload files to"))
    timezone = TimeZoneField(default='UTC', verbose_name=_("Timezone to use for date/time"),
                             help_text=_("Timezone used by the station for recording observations"))
    
    write_mode = models.CharField(max_length=20, choices=WRITE_MODES, default="append",
                                  verbose_name=_("FTP Write Mode"))
    
    panels = DispatchChannel.base_panels + [
        MultiFieldPanel([
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("user"),
            FieldPanel("password"),
            FieldPanel("directory"),
            FieldPanel("write_mode"),
        ], heading=_("FTP Configuration")),
    ] + DispatchChannel.parameter_panels
    
    class Meta:
        verbose_name = _("FTP Upload")
        verbose_name_plural = _("FTP Uploads")
    
    def send_data(self, data_records):
        return upload_to_ftp(self, data_records)
    
    @property
    def connection_details(self):
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
        }
