import logging
import os
import tempfile

from adl.core.utils import get_object_or_none
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from wagtail.admin import messages

from .forms import TestCSVConfigForm
from .ftp import FTPError
from .ftp.ftp_utils import clean_remote_path, get_ftp_dir_list
from .models import NetworkFTP

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
        directories = get_ftp_dir_list(ftp_client, remote_path)
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
                            all_data_columns = sorted([col for col in all_columns if col not in ['observation_time']])
                            
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
