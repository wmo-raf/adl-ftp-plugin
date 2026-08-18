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


class DecoderVariableMappingRowForm(forms.Form):
    """
    One row of the "Populate variable mappings from decoder" review form.

    An empty ``file_variable_unit`` means "create a Unit for the decoder's
    declared symbol"; an empty ``adl_parameter`` means "create a DataParameter
    named after the decoder's label, with its declared ADL unit". Row-level
    validation only guards against picking a parameter whose unit cannot be
    converted from the file unit.
    """

    include = forms.BooleanField(required=False, initial=True, label=_("Include"))
    name = forms.CharField(widget=forms.HiddenInput)
    file_variable_unit = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label=_("File Variable Unit"),
    )
    adl_parameter = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label=_("ADL Parameter"),
    )

    def __init__(self, *args, variables_by_name=None, **kwargs):
        from adl.core.models import DataParameter, Unit

        super().__init__(*args, **kwargs)
        self.variables_by_name = variables_by_name or {}

        self.fields["file_variable_unit"].queryset = Unit.objects.all()
        self.fields["adl_parameter"].queryset = DataParameter.objects.select_related("unit")

        name = self.initial.get("name")
        if name is None and self.is_bound:
            name = self.data.get(self.add_prefix("name"))
        self.variable = self.variables_by_name.get(name)

        if self.variable:
            self.fields["file_variable_unit"].empty_label = _("Create unit '%(symbol)s'") % {
                "symbol": self.variable["unit"]
            }
            self.fields["adl_parameter"].empty_label = _("Create new: %(label)s (%(unit)s)") % {
                "label": self.variable["label"],
                "unit": self.variable["adl_unit"],
            }

    def clean(self):
        cleaned = super().clean()
        variable = self.variables_by_name.get(cleaned.get("name"))
        if variable is None:
            raise forms.ValidationError(_("Unknown decoder variable '%(name)s'.") % {"name": cleaned.get("name")})
        cleaned["variable"] = variable

        if not cleaned.get("include"):
            return cleaned

        parameter = cleaned.get("adl_parameter")
        if parameter is not None and not parameter.custom_unit_context and not parameter.is_coded:
            file_unit = cleaned.get("file_variable_unit")
            file_symbol = file_unit.symbol if file_unit else variable["unit"]
            try:
                from adl.core.units import units
                if units(file_symbol).dimensionality != units(parameter.unit.symbol).dimensionality:
                    self.add_error(
                        "adl_parameter",
                        _("'%(file)s' cannot be converted to '%(param)s'.") % {
                            "file": file_symbol,
                            "param": parameter.unit.symbol,
                        },
                    )
            except Exception:
                # Unknown symbol -> the unit creation step will report it
                pass
        return cleaned


DecoderVariableMappingFormSet = forms.formset_factory(DecoderVariableMappingRowForm, extra=0)
