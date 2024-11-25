from ..registries import FTPDecoder


class SiapMicrosDecoder(FTPDecoder):
    """
    This class represents a decoder for the TOA5 data format.
    """
    
    type = "siapmicros"
    compat_type = "siapmicros"
    display_name = "SIAP+Micros"
