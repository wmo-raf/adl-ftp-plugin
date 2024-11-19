from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import MultiFieldPanel, FieldPanel
from wagtail.models import Orderable
from wis2box_adl.core.models import Network, Station, DataParameter


class NetworkFTP(ClusterableModel):
    network = models.OneToOneField(Network, on_delete=models.CASCADE, verbose_name=_("Network"))
    host = models.CharField(max_length=255, verbose_name=_("Host"))
    port = models.IntegerField(verbose_name=_("Port"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"))
    
    decoder = models.CharField(max_length=255, verbose_name=_("Decoder"))
    
    panels = [
        MultiFieldPanel([
            FieldPanel("network"),
            FieldPanel("host"),
            FieldPanel("port"),
            FieldPanel("username"),
            FieldPanel("password"),
        ], heading=_("FTP Settings")),
        
        FieldPanel("decoder"),
    ]


class FTPVariableMapping(Orderable):
    network_ftp = ParentalKey(NetworkFTP, on_delete=models.CASCADE, related_name="variable_mappings")
    file_variable_name = models.CharField(max_length=255, verbose_name=_("File Variable Name"))
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("Variable"))
    
    panels = [
        FieldPanel("file_variable_name"),
        FieldPanel("adl_parameter"),
    ]


class FTPStationLink(models.Model):
    network_ftp = models.OneToOneField(NetworkFTP, on_delete=models.CASCADE, verbose_name=_("Network FTP"))
    station = models.OneToOneField(Station, on_delete=models.CASCADE, verbose_name=_("FTP Station"))
    ftp_path = models.CharField(max_length=255, verbose_name=_("FTP Path"))
    file_pattern = models.CharField(max_length=255, verbose_name=_("File Pattern"))
