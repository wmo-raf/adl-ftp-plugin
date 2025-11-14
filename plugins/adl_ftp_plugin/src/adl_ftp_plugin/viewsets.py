from django.urls import path
from wagtail.admin.viewsets import ViewSetGroup
from wagtail.admin.viewsets.base import ViewSet
from wagtail.admin.viewsets.chooser import ChooserViewSet
from wagtail.admin.viewsets.model import ModelViewSet

from .models import StandardCSVConfig
from .views import test_decoder_config


class StandardCSVConfigViewSet(ModelViewSet):
    model = StandardCSVConfig
    icon = "doc-full"
    add_to_settings_menu = False


class StandardCSVConfigChooserViewSet(ChooserViewSet):
    model = StandardCSVConfig
    icon = "doc-full"
    choose_one_text = "Choose CSV Configuration"
    choose_another_text = "Choose different CSV Configuration"
    edit_item_text = "Edit this CSV Configuration"


standard_csv_config_chooser_viewset = StandardCSVConfigChooserViewSet("choose-standard-csv-config")


class TestDecoderConfigViewSet(ViewSet):
    menu_label = "Test Decoder Config"
    icon = "cog"
    name = "adl-ftp-plugin/test-decoder-config"
    
    def get_urlpatterns(self):
        return [
            path('', test_decoder_config, name='test_decoder_config'),
        ]


class FTPSettingsViewSetGroup(ViewSetGroup):
    menu_label = "FTP Settings"
    menu_icon = "cog"
    add_to_settings_menu = True
    menu_order = 900
    
    items = [
        StandardCSVConfigViewSet(),
        TestDecoderConfigViewSet(),
    ]
