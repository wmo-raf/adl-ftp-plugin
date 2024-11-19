from wis2box_adl.core.registries import Plugin


class PluginNamePlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"

    def get_urls(self):
        return []

    def get_data(self):
        return []
