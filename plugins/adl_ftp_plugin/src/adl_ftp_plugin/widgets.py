from django.forms import widgets
from django.urls import reverse
from django.forms import RadioSelect

from .registries import ftp_decoder_registry


class FTPDecoderSelectWidget(widgets.Select):
    def __init__(self, attrs=None, choices=()):
        blank_choice = [("", "---------")]

        decoder_choices = [(decoder.type, decoder.display_name) for decoder in ftp_decoder_registry.registry.values()]

        super().__init__(attrs, blank_choice + decoder_choices)

    class Media:
        js = ('adl_ftp_plugin/js/ftp_decoder_type_conditional_fields.js',)


class ConnectionTypeRadioSelect(RadioSelect):
    """Radio select for connection type that triggers conditional field visibility"""

    class Media:
        js = ('adl_ftp_plugin/js/ftp_connection_type_conditional_fields.js',)


class FTPDirectoryTreeSelectWidget(widgets.Widget):
    template_name = "adl_ftp_plugin/widgets/ftp_directory_tree_select_widget.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)

        context.update({
            'ftp_connection_dir_list_url': reverse("get_ftp_connection_dir_list"),
        })

        return context

    class Media:
        js = ('adl_ftp_plugin/vue/tree-widget.js',)


class ConditionalRadioSelect(RadioSelect):
    """Radio select that triggers conditional field visibility"""

    class Media:
        js = ('adl_ftp_plugin/js/csv_config_conditional_fields.js',)
