from django.templatetags.static import static
from django.urls import path
from django.utils.html import format_html
from wagtail import hooks
from wagtail.admin.filters import WagtailFilterSet
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import FTPStationDataFile
from .views import (
    direct_fetch_file_check,
    direct_fetch_file_list,
    get_ftp_connection_dir_list,
    populate_variable_mappings_from_decoder,
)
from .viewsets import FTPSettingsViewSetGroup, standard_csv_config_chooser_viewset


@hooks.register("insert_editor_js")
def insert_editor_js():
    return format_html(
        '<script src="{}"></script>', static("adl_ftp_plugin/js/ftp_conditional_fields.js"),
    )


@hooks.register('register_admin_urls')
def urlconf_adl_ftp_plugin():
    return [
        path("adl-ftp-plugin/conn-ftp-list/", get_ftp_connection_dir_list,
             name="get_ftp_connection_dir_list"),
        path("adl-ftp-plugin/connections/<int:connection_id>/populate-variable-mappings/",
             populate_variable_mappings_from_decoder,
             name="populate_variable_mappings_from_decoder"),
        path("adl-ftp-plugin/station-links/<int:station_link_id>/direct-fetch-files/",
             direct_fetch_file_list,
             name="ftp_direct_fetch_file_list"),
        path("adl-ftp-plugin/station-links/<int:station_link_id>/direct-fetch-files/check/",
             direct_fetch_file_check,
             name="ftp_direct_fetch_file_check"),
    ]


@hooks.register("register_admin_viewset")
def register_ftp_viewset():
    return [
        standard_csv_config_chooser_viewset,
        FTPSettingsViewSetGroup()
    ]


class FTPStationDataFileFilterSet(WagtailFilterSet):
    class Meta:
        model = FTPStationDataFile
        fields = ["station_link"]


class FTPStationDataFileViewSet(SnippetViewSet):
    model = FTPStationDataFile
    icon = "user"
    list_per_page = 50
    inspect_view_enabled = True
    filterset_class = FTPStationDataFileFilterSet


register_snippet(FTPStationDataFileViewSet)
