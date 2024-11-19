from django.apps import AppConfig

from wis2box_adl.core.registries import plugin_registry
from .registries import ftp_decoder_registry


class PluginNameConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "adl_ftp_plugin"
    
    def ready(self):
        from .plugins import PluginNamePlugin
        
        plugin_registry.register(PluginNamePlugin())
