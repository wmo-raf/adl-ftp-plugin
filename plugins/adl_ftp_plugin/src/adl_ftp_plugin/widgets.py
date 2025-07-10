from django.forms import widgets
from django.urls import reverse

from .registries import ftp_decoder_registry


class FTPDecoderSelectWidget(widgets.Select):
    def __init__(self, attrs=None, choices=()):
        blank_choice = [("", "---------")]
        
        decoder_choices = [(decoder.type, decoder.display_name) for decoder in ftp_decoder_registry.registry.values()]
        
        super().__init__(attrs, blank_choice + decoder_choices)


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
