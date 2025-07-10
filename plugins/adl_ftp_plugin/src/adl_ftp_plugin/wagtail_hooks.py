from django.templatetags.static import static
from django.urls import path
from django.utils.html import format_html
from wagtail import hooks

from .views import get_ftp_connection_dir_list


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
    ]
