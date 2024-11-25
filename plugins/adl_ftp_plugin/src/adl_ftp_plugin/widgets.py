from django.forms import widgets

from .registries import ftp_decoder_registry


class FTPDecoderSelectWidget(widgets.Select):
    def __init__(self, attrs=None, choices=()):
        blank_choice = [("", "---------")]
        
        decoder_choices = [(decoder.type, decoder.display_name) for decoder in ftp_decoder_registry.registry.values()]
        
        print(decoder_choices)
        
        super().__init__(attrs, blank_choice + decoder_choices)
