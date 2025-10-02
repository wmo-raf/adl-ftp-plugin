from django import forms
from django.utils.translation import gettext_lazy as _

from .models import NetworkFTP


class TestCSVConfigForm(forms.Form):
    """Form for testing decoder configuration"""
    
    connection = forms.ModelChoiceField(
        queryset=NetworkFTP.objects.none(),  # Will be set in __init__
        empty_label=_("Select a connection"),
        label=_("Connection"),
        help_text=_("Select a connection with a decoder configured"),
        widget=forms.Select(attrs={'class': 'connection-select'})
    )
    
    data_file = forms.FileField(
        label=_("Data File"),
        help_text=_("Select a data file to test parsing"),
        widget=forms.FileInput(attrs={
            'accept': '.csv,.txt,.dat',
            'class': 'data-file-input'
        })
    )
    
    show_only_mapped = forms.BooleanField(
        required=False,
        initial=False,
        label=_("Show only mapped variables"),
        help_text=_("If checked, only columns with variable mappings will be displayed in the results")
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Filter connections to only those with a decoder set
        self.fields['connection'].queryset = NetworkFTP.objects.filter(
            decoder__isnull=False
        ).exclude(decoder='').select_related('network', 'csv_config')
    
    def clean_data_file(self):
        """Validate the uploaded data file"""
        data_file = self.cleaned_data.get('data_file')
        
        if data_file:
            # Check file size (max 10MB)
            if data_file.size > 10 * 1024 * 1024:
                raise forms.ValidationError(
                    _("File size must not exceed 10MB")
                )
        
        return data_file
