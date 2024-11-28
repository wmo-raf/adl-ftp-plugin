from django.apps import AppConfig
from adl.core.registries import plugin_registry

from .registries import ftp_decoder_registry


class PluginNameConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "adl_ftp_plugin"
    
    def ready(self):
        from .plugins import AdlFtpPlugin
        
        plugin_registry.register(AdlFtpPlugin())
        
        from .decoders import Toa5Decoder, SiapMicrosDecoder
        ftp_decoder_registry.register(Toa5Decoder())
        ftp_decoder_registry.register(SiapMicrosDecoder())
