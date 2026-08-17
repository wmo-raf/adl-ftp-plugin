import logging
import os
import tempfile
from datetime import datetime

from adl.core.utils import get_object_or_none, get_url_for_station_link
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods
from wagtail.admin.paginator import WagtailPaginator
from wagtail.permission_policies import ModelPermissionPolicy
from wagtail.admin import messages

from .decoder_variables import (
    create_variable_mappings,
    find_parameter_for_variable,
    find_unit_by_symbol,
    get_decoder_variables,
    get_unmapped_decoder_variables,
)
from .forms import DecoderVariableMappingFormSet, TestCSVConfigForm
from .ftp import FTPError
from .ftp.ftp_utils import clean_remote_path, get_ftp_dir_list, parse_date_from_filename
from .models import FTPListingStrategy, FTPStationDataFile, FTPStationLink, NetworkFTP

logger = logging.getLogger(__name__)


def get_ftp_connection_dir_list(request):
    connection_id = request.GET.get("connection_id")
    if not connection_id:
        return JsonResponse({"error": "Missing connection_id"}, status=400)
    
    connection = get_object_or_none(NetworkFTP, id=connection_id)
    if not connection:
        return JsonResponse({"error": "Connection not found"}, status=404)
    
    remote_path = request.GET.get("remote_path", "/")
    root_request = request.GET.get("root_request", "false").lower() == "true"
    
    remote_path = clean_remote_path(remote_path, force_root=root_request)
    if remote_path is None:
        return JsonResponse({"error": "Invalid remote path"}, status=400)
    
    ftp_client = None
    try:
        ftp_client = connection.get_client()
        directories = get_ftp_dir_list(ftp_client, remote_path, root_request=root_request)
        ftp_client.close()
        return JsonResponse({"directories": directories}, status=200)
    except FTPError as e:
        if ftp_client is not None:
            ftp_client.close()
        
        return JsonResponse({"error": e.message}, status=e.status)
    except Exception as e:
        if ftp_client is not None:
            ftp_client.close()
        return JsonResponse({"error": str(e)}, status=400)


def column_sort_key(col):
    parts = col.rsplit('_', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return (parts[0], int(parts[1]))
    return (col, 0)


@require_http_methods(["GET", "POST"])
def test_decoder_config(request):
    """Test decoder configuration by parsing an uploaded file"""
    
    parsed_data = None
    error = None
    temp_file_path = None
    
    if request.method == 'POST':
        form = TestCSVConfigForm(request.POST, request.FILES)
        
        if form.is_valid():
            connection = form.cleaned_data['connection']
            uploaded_file = form.cleaned_data['data_file']
            show_only_mapped = form.cleaned_data['show_only_mapped']
            decoder_name = connection.decoder
            
            try:
                # Get the decoder from the registry
                decoder = connection.get_decoder()
                
                if not decoder:
                    error = f"Decoder '{decoder_name}' not found in registry"
                else:
                    # Save uploaded file to temporary location
                    with tempfile.NamedTemporaryFile(mode='wb', suffix=uploaded_file.name, delete=False) as temp_file:
                        for chunk in uploaded_file.chunks():
                            temp_file.write(chunk)
                        temp_file_path = temp_file.name
                    
                    # Set CSV config if using standard_csv decoder
                    if decoder_name == "standard_csv":
                        if not connection.csv_config:
                            error = "Standard CSV decoder selected but no CSV configuration set"
                        else:
                            decoder._config = connection.csv_config
                    
                    if not error:
                        # Parse the file
                        parsed_result = decoder.decode(temp_file_path)
                        
                        # Get variable mappings
                        variable_mappings = connection.variable_mappings.all().select_related(
                            'adl_parameter', 'file_variable_unit'
                        )
                        
                        # Create mapping dict for display
                        mapping_dict = {
                            vm.file_variable_name: {
                                'adl_parameter': vm.adl_parameter.name,
                                'unit': vm.file_variable_unit.symbol if vm.file_variable_unit else 'N/A'
                            }
                            for vm in variable_mappings
                        }
                        
                        # Get mapped column names
                        mapped_columns = set(mapping_dict.keys())
                        
                        # Prepare data for display
                        values = parsed_result.get('values', [])
                        header = parsed_result.get('header', {})
                        metadata = parsed_result.get('metadata', {})
                        
                        if values:
                            # Get all unique column names from the parsed data
                            all_columns = set()
                            for record in values:
                                all_columns.update(record.keys())
                            
                            # Remove observation_time
                            all_data_columns = sorted(
                                [col for col in all_columns if col not in ['observation_time']],
                                key=column_sort_key
                            )
                            
                            if show_only_mapped:
                                data_columns = [col for col in all_data_columns if col in mapped_columns]
                                # Filter metadata to only show mapped columns
                                if metadata:
                                    metadata = {k: v for k, v in metadata.items() if
                                                k in mapped_columns or k == 'TIMESTAMP'}
                            else:
                                data_columns = all_data_columns
                            
                            parsed_data = {
                                'records': values[:100],  # Limit to first 100 records for display
                                'total_records': len(values),
                                'columns': data_columns,
                                'all_columns_count': len(all_data_columns),
                                'mapped_columns_count': len(mapped_columns),
                                'mapping': mapping_dict,
                                'connection_name': connection.network.name,
                                'decoder_name': decoder.display_name,
                                'header': header,
                                'metadata': metadata,
                                'show_only_mapped': show_only_mapped,
                            }
                            
                            # Add CSV config name if applicable
                            if decoder_name == "standard_csv" and connection.csv_config:
                                parsed_data['csv_config_name'] = connection.csv_config.name
                            
                            messages.success(
                                request,
                                f"Successfully parsed {len(values)} records from the file using {decoder.display_name} decoder"
                            )
                        else:
                            error = "No data was parsed from the file. Please check your decoder configuration."
            
            except Exception as e:
                logger.exception(f"Error parsing data file: {e}")
                error = f"Error parsing data file: {str(e)}"
            
            finally:
                # Clean up temp file
                if temp_file_path:
                    try:
                        os.unlink(temp_file_path)
                    except:
                        pass
        
        # Get connection_id from GET parameter
    connection_id = request.GET.get('connection_id')
    initial_data = {}
    
    # If connection_id is provided, set it as initial value
    if connection_id:
        try:
            connection = NetworkFTP.objects.get(
                id=connection_id,
                decoder__isnull=False
            )
            initial_data['connection'] = connection
        except (NetworkFTP.DoesNotExist, ValueError):
            messages.warning(
                request,
                f"Connection with ID {connection_id} not found or has no decoder configured"
            )
    
    form = TestCSVConfigForm(initial=initial_data)
    context = {
        'form': form,
        "page_title": "Test Decoder Configuration",
        'parsed_data': parsed_data,
        'error': error,
    }
    
    return render(request, 'adl_ftp_plugin/test_decoder_config.html', context)


def _user_can_manage_connection(user, connection):
    try:
        from adl.core.permissions import can_manage_connection
    except ImportError:  # older core without the helper
        return user.is_superuser or user.has_perm("adl_ftp_plugin.change_networkftp")
    return can_manage_connection(user, connection)


@require_http_methods(["GET", "POST"])
def populate_variable_mappings_from_decoder(request, connection_id):
    """
    Review-and-create page that seeds a connection's variable mappings from the
    variables its decoder declares via ``FTPDecoder.get_variables()``.

    GET renders one row per declared variable that is not yet mapped, with the
    file unit and ADL parameter pre-selected where an existing match is found.
    POST creates the missing units/parameters and the mapping rows in one
    transaction, then returns to the connection edit page.
    """
    connection = get_object_or_404(NetworkFTP, pk=connection_id)
    
    if not _user_can_manage_connection(request.user, connection):
        raise PermissionDenied
    
    variables = get_decoder_variables(connection)
    if not variables:
        messages.warning(
            request,
            _("Decoder '%(decoder)s' does not declare any variables, so mappings cannot be pre-populated.") % {
                "decoder": connection.decoder
            },
        )
        return redirect(connection.edit_url)
    
    unmapped = get_unmapped_decoder_variables(connection, variables)
    variables_by_name = {v["name"]: v for v in variables}
    form_kwargs = {"variables_by_name": variables_by_name}
    
    if request.method == "POST":
        formset = DecoderVariableMappingFormSet(request.POST, form_kwargs=form_kwargs)
        if formset.is_valid():
            rows = [
                {
                    "variable": form.cleaned_data["variable"],
                    "file_variable_unit": form.cleaned_data.get("file_variable_unit"),
                    "adl_parameter": form.cleaned_data.get("adl_parameter"),
                }
                for form in formset
                if form.cleaned_data.get("include")
            ]
            try:
                summary = create_variable_mappings(connection, rows)
            except ValidationError as e:
                messages.error(request, _("Could not create variable mappings: %(error)s") % {
                    "error": "; ".join(e.messages)
                })
            else:
                messages.success(
                    request,
                    _("Created %(created)s variable mapping(s) — %(units)s new unit(s), "
                      "%(params)s new parameter(s); %(skipped)s already mapped.") % {
                        "created": summary["created"],
                        "units": len(summary["units_created"]),
                        "params": len(summary["parameters_created"]),
                        "skipped": summary["skipped_existing"],
                    },
                )
                return redirect(connection.edit_url)
    else:
        initial = [
            {
                "name": v["name"],
                "include": True,
                "file_variable_unit": find_unit_by_symbol(v["unit"]),
                "adl_parameter": find_parameter_for_variable(v),
            }
            for v in unmapped
        ]
        formset = DecoderVariableMappingFormSet(initial=initial, form_kwargs=form_kwargs)
    
    rows = [(form, form.variable) for form in formset]
    page_title = _("Populate variable mappings from decoder")
    
    context = {
        "breadcrumbs_items": [
            {"url": reverse("wagtailadmin_home"), "label": _("Home")},
            {"url": reverse("connections_list"), "label": _("Network Connections")},
            {"url": connection.edit_url, "label": connection.name},
            {"url": None, "label": page_title},
        ],
        "header_title": page_title,
        "header_icon": "cog",
        "connection": connection,
        "decoder_name": connection.get_decoder_display(),
        "formset": formset,
        "rows": rows,
        "total_variables": len(variables),
        "already_mapped_count": len(variables) - len(unmapped),
    }
    
    return render(request, "adl_ftp_plugin/populate_variable_mappings.html", context)


# ---------------------------------------------------------------------------
# Direct-fetch file list
# ---------------------------------------------------------------------------

DIRECT_FETCH_FILES_PER_PAGE = 200

# The same gate Wagtail's inspect view applies to the station link model —
# the page hangs off the inspect page and shows nothing more sensitive
STATION_LINK_VIEW_PERMISSIONS = ["add", "change", "delete", "view"]


def _user_can_view_station_link(user, station_link):
    policy = ModelPermissionPolicy(type(station_link))
    return policy.user_has_any_permission(user, STATION_LINK_VIEW_PERMISSIONS)


def _parse_window_bound(raw, tz):
    """
    Parse a ``from``/``to`` query value into an aware datetime. Accepts what a
    ``datetime-local`` input or a hand-typed ISO string produces; a bare date
    means midnight. Naive values are read in ``tz`` (the station timezone,
    which is what the resolved window is shown in). Returns ``(value, error)``.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    value = parse_datetime(raw)
    if value is None:
        date = parse_date(raw)
        if date is not None:
            value = datetime.combine(date, datetime.min.time())
    if value is None:
        return None, _("Could not read '%(raw)s' as a date or datetime.") % {"raw": raw}
    if dj_timezone.is_naive(value):
        value = dj_timezone.make_aware(value, tz)
    return value, None


def _direct_fetch_start_source(plugin, station_link):
    """Which rule produced the default window start — mirrors the priority in
    ``Plugin.get_dates_for_station()`` so the page can name it."""
    if plugin.get_start_date_from_db(station_link):
        return _("latest saved observation for this station")
    if station_link.get_first_collection_date():
        return _("the station link's Start Date")
    return _("now (no saved observations and no Start Date set)")


@require_GET
def direct_fetch_file_list(request, station_link_id):
    """
    Preview page for a ``DIRECT_FETCH`` station link: the remote paths the
    next ingestion run would try, in the order it would try them, plus
    whether ADL already holds each file. Pure computation — it never opens
    an FTP/SFTP connection, so it is safe to reload freely.

    Defaults to the real next-run window; ``?from=``/``?to=`` preview a
    different range without touching the station link.
    """
    station_link = get_object_or_404(FTPStationLink, pk=station_link_id)
    if not _user_can_view_station_link(request.user, station_link):
        raise PermissionDenied

    page_title = _("Direct Fetch Files")
    inspect_url = get_url_for_station_link(station_link, "inspect", takes_args=True)
    context = {
        "breadcrumbs_items": [
            {"url": reverse("wagtailadmin_home"), "label": _("Home")},
            {"url": get_url_for_station_link(station_link, "index"), "label": _("Station Links")},
            {"url": inspect_url, "label": str(station_link)},
            {"url": None, "label": page_title},
        ],
        "header_title": _("%(title)s — %(station)s") % {"title": page_title, "station": station_link},
        "header_icon": "doc-full-inverse",
        "station_link": station_link,
        "back_url": inspect_url,
        "is_direct_fetch": station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH,
    }
    if not context["is_direct_fetch"]:
        context["strategy_label"] = station_link.get_listing_strategy_display()
        return render(request, "adl_ftp_plugin/direct_fetch_file_list.html", context)

    plugin = station_link.network_connection.get_plugin()
    tz = station_link.timezone
    default_start, default_end = plugin.get_dates_for_station(station_link)

    errors = []
    start_date, err = _parse_window_bound(request.GET.get("from"), tz)
    if err:
        errors.append(err)
    end_date, err = _parse_window_bound(request.GET.get("to"), tz)
    if err:
        errors.append(err)
    overridden = bool(start_date or end_date)
    start_date = start_date or default_start
    end_date = end_date or default_end
    if start_date > end_date:
        errors.append(_("The window start is after its end; nothing to list."))

    file_paths = []
    if not errors:
        file_paths = plugin.get_station_file_paths(station_link, start_date, end_date)

    paginator = WagtailPaginator(file_paths, DIRECT_FETCH_FILES_PER_PAGE)
    page = paginator.get_page(request.GET.get("p"))

    # Local status only for the visible page: one query, keyed by file name
    # (the same key _process_file() uses to decide whether to download)
    page_names = [os.path.basename(path) for path in page.object_list]
    local_files = {
        f.file_name: f
        for f in FTPStationDataFile.objects.filter(
            station_link=station_link, file_name__in=page_names
        )
    }
    filename_tz = station_link.direct_fetch_datetime_timezone
    rows = []
    for offset, path in enumerate(page.object_list):
        name = os.path.basename(path)
        rows.append({
            "index": page.start_index() + offset,
            "path": path,
            "directory": os.path.dirname(path),
            "file_name": name,
            "file_datetime": parse_date_from_filename(
                name, station_link.direct_fetch_datetime_format, filename_tz
            ),
            "local_file": local_files.get(name),
        })

    context.update({
        "errors": errors,
        "start_date": dj_timezone.localtime(start_date, tz),
        "end_date": dj_timezone.localtime(end_date, tz),
        "station_tz": str(tz),
        "window_overridden": overridden,
        "start_source": _direct_fetch_start_source(plugin, station_link),
        "from_value": request.GET.get("from", ""),
        "to_value": request.GET.get("to", ""),
        "total_files": paginator.count,
        "total_directories": len({os.path.dirname(p) for p in file_paths}),
        "page_obj": page,
        "elided_page_range": paginator.get_elided_page_range(page.number),
        "rows": rows,
        "filename_tz": str(filename_tz),
    })
    return render(request, "adl_ftp_plugin/direct_fetch_file_list.html", context)
