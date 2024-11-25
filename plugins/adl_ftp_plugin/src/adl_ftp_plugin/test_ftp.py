from wis2box_adl.core.registries import plugin_registry
from wis2box_adl.core.models import Network


def test():
    ftp_plugin = plugin_registry.get("adl_ftp_plugin")
    # network = Network.objects.first()
    
    network = Network.objects.get(pk=2)
    
    ftp_plugin.run_process(network)
