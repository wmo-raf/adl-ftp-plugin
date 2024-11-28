from adl.core.models import DataParameter
from adl.core.models import NetworkConnection, StationLink
from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable
from wagtail.snippets.models import register_snippet

from adl_ftp_plugin.utils import get_ftp_decoder_choices


@register_snippet
class NetworkFTP(NetworkConnection):
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
    
    def __str__(self):
        return f"{self.network} FTP"


@register_snippet
class FTPVariableMapping(Orderable):
    network_ftp = ParentalKey(NetworkFTP, on_delete=models.CASCADE, related_name="variable_mappings")
    file_variable_name = models.CharField(max_length=255, verbose_name=_("File Variable Name"))
    file_variable_units = models.CharField(max_length=255, verbose_name=_("File Variable Units"))
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Variable"))
    
    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("file_variable_name"),
        FieldPanel("file_variable_units"),
    ]


@register_snippet
class FTPStationLink(StationLink):
    DATE_GRANULARITY_CHOICES = [
        ("year", _("Year")),
        ("month", _("Month")),
        ("day", _("Day")),
    ]
    
    ftp_path = models.CharField(max_length=255, verbose_name=_("FTP Path"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
    dir_structured_by_date = models.BooleanField(default=False, verbose_name=_("Directory Structured by Date ?"))
    date_granularity = models.CharField(max_length=255, blank=True, null=True, choices=DATE_GRANULARITY_CHOICES,
                                        verbose_name=_("Date Granularity"))
    skip_already_processed_files = models.BooleanField(default=True,
                                                       verbose_name=_("Skip already downloaded and processed Files"))
    
    panels = StationLink.panels + [
        MultiFieldPanel([
            FieldPanel("ftp_path"),
            FieldPanel("file_pattern"),
        ], heading=_("FTP Configuration")),
        MultiFieldPanel([
            FieldPanel("dir_structured_by_date"),
            FieldPanel("date_granularity"),
        ], heading=_("File Structure")),
        FieldPanel("skip_already_processed_files"),
    ]
    
    class Meta:
        verbose_name = _("FTP Station Link")
        verbose_name_plural = _("FTP Station Links")
    
    def __str__(self):
        return f"{self.network_connection} - {self.station}"


def get_ftp_data_file_upload_path(instance, filename):
    return f"ftp_data_files/{instance.station_link.network_connection.network.id}/{instance.station_link.station.id}/{filename}"


@register_snippet
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
