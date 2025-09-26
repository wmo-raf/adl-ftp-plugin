from adl.core.utils import get_object_or_none
from django.http import JsonResponse

from .ftp import FTPClient, FTPError
from .ftp.ftp_utils import clean_remote_path, get_ftp_dir_list
from .models import NetworkFTP


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
