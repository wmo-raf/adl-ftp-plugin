from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable
from wagtail.snippets.models import register_snippet
from wis2box_adl.core.models import Network, Station, DataParameter

from adl_ftp_plugin.utils import get_ftp_decoder_choices


@register_snippet
class NetworkFTP(ClusterableModel):
    network = models.OneToOneField(Network, on_delete=models.CASCADE, verbose_name=_("Network"))
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.IntegerField(verbose_name=_("Port"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"))
    
    decoder = models.CharField(max_length=255, choices=get_ftp_decoder_choices, verbose_name=_("Decoder"))
    
    extract_date_from_filename = models.BooleanField(default=False,
                                                     verbose_name=_("Extract Observation Date from Filename"))
    
    panels = [
        MultiFieldPanel([
            FieldPanel("network"),
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("username"),
            FieldPanel("password"),
        ], heading=_("FTP Settings")),
        
        FieldPanel("decoder"),
        FieldPanel("extract_date_from_filename"),
        
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
class FTPStationLink(models.Model):
    network_ftp = models.ForeignKey(NetworkFTP, on_delete=models.CASCADE, verbose_name=_("Network FTP"),
                                    related_name="stations")
    station = models.OneToOneField(Station, on_delete=models.CASCADE, verbose_name=_("FTP Station"))
    ftp_path = models.CharField(max_length=255, verbose_name=_("FTP Path"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
    
    class Meta:
        verbose_name = _("FTP Station Link")
        verbose_name_plural = _("FTP Station Links")
    
    def __str__(self):
        return f"{self.network_ftp} - {self.station}"
