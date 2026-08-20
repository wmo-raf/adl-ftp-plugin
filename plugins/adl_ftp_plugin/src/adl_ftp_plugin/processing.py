"""
Turning one staged file into records ADL has actually saved.

This is the half of the ingestion pipeline that does not care where the file
came from: it decodes a file already on local disk, yields its records, waits
for core to persist them, and only then records that the file was processed.
The FTP plugin reaches it after downloading; a consumer that receives files by
upload reaches it after staging (wmo-raf/adl#271).

The staged file is duck-typed — everything used of it is:

- ``file.path``   the local path to decode
- ``file_name``   for log messages (optional; the path's basename otherwise)
- ``processed_at`` / ``values_saved``  stamped here
- ``save(update_fields=...)``

The values-saved count is handed over the same way core hands anything to a
plugin: through an attribute on the station link. The generator zeroes it
before a file's records go out and reads it back once core has consumed the
trailing ``FLUSH``, so what is stamped is what reached the database for *this*
file — not what the decoder produced.
"""

import logging
import os

from django.utils import timezone as dj_timezone

try:
    # ADL >= 0.8.12: yielded between records to have core persist what is
    # buffered before the generator is resumed. On an older core the marker
    # does not exist and files are stamped without that guarantee.
    from adl.core.registries import FLUSH
except ImportError:  # pragma: no cover - older core
    FLUSH = None

logger = logging.getLogger(__name__)

VALUES_SAVED_ATTR = "_adl_file_values_saved"

LEGACY_VALUES_SAVED_ATTR = "_adl_ftp_values_saved"
"""The name this counter shipped under while it was FTP-only. Kept in step so
anything still reading it — an old subclass, a test double — sees the same
number."""

_VALUES_SAVED_ATTRS = (VALUES_SAVED_ATTR, LEGACY_VALUES_SAVED_ATTR)


def reset_values_saved(station_link):
    """Open a file's counting window: from here, count from zero."""
    for attr in _VALUES_SAVED_ATTRS:
        setattr(station_link, attr, 0)


def add_values_saved(station_link, count):
    """
    Add what core just saved to the open window, if there is one.

    Called from a plugin's ``after_save_records``. Outside a window — core
    saving records that did not come from a staged file — there is nothing to
    add to and nothing happens.
    """
    for attr in _VALUES_SAVED_ATTRS:
        if hasattr(station_link, attr):
            setattr(station_link, attr, getattr(station_link, attr) + count)


def read_values_saved(station_link):
    """What the open window has counted so far."""
    return getattr(station_link, VALUES_SAVED_ATTR, 0)


def decode_and_stamp(data_file, decoder, station_link, task_logger=None):
    """
    Generator that decodes one staged file and yields its records.

    Decodes ``data_file``, yields each record, then yields core's ``FLUSH``
    marker so the records are persisted before this generator is resumed. Only
    after that is the file stamped ``processed_at`` — "processed" means its
    data is in the database, not merely that it decoded — along with
    ``values_saved``, the number of observation values core reported upserting
    for it.

    A file that fails to decode is left unstamped, so it still shows as
    received-but-unprocessed and can be retried or diagnosed.

    :param data_file: The staged file (see this module's docstring for the
        interface it must satisfy)
    :param decoder: Anything answering ``decode(file_path)``, e.g. a
        :class:`~adl_ftp_plugin.decoder_resolution.ConfiguredDecoder`
    :param station_link: The station link the file belongs to; carries the
        values-saved handover
    :param task_logger: Where to report progress and failures; defaults to this
        module's logger
    :return: Whether the file decoded and was stamped
    :rtype: bool
    """
    log = task_logger or logger
    file_name = getattr(data_file, "file_name", None) or os.path.basename(data_file.file.path)

    try:
        data = decoder.decode(data_file.file.path)
        file_records = data.get("values", [])
    except Exception as e:
        log.error(f"Error decoding file {file_name}: {e}")
        return False

    log.debug(f"Decoded {len(file_records)} records from {file_name}")

    reset_values_saved(station_link)

    for record in file_records:
        yield record

    if FLUSH is not None:
        # Resumes only once core has persisted this file's records
        yield FLUSH
        values_saved = read_values_saved(station_link)
    else:
        # Older core: chunks may span files, so the per-file count would
        # be attributed to whichever file happens to be current — leave
        # it unrecorded rather than record something misleading
        values_saved = None

    data_file.processed_at = dj_timezone.now()
    data_file.values_saved = values_saved
    data_file.save(update_fields=['processed_at', 'values_saved'])

    if values_saved == 0:
        log.warning(
            f"File {file_name} decoded {len(file_records)} record(s) but none of its "
            f"values were saved — check the variable mappings and the ingestion window"
        )

    return True
