from django.templatetags.static import static
from django.utils.html import format_html
from wagtail import hooks


@hooks.register("insert_editor_js")
def insert_editor_js():
    return format_html(
        '<script src="{}"></script>', static("adl_ftp_plugin/js/ftp_conditional_fields.js"),
    )
