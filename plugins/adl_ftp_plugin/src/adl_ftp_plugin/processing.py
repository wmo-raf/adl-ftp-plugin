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
from datetime import datetime

from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone as dj_timezone

try:
    # ADL >= 0.8.12: yielded between records to have core persist what is
    # buffered before the generator is resumed. On an older core the marker
    # does not exist and files are stamped without that guarantee.
    from adl.core.registries import FLUSH
except ImportError:  # pragma: no cover - older core
    FLUSH = None

logger = logging.getLogger(__name__)

VALUES_SAVED_ATTR = "_adl_ftp_values_saved"
"""The attribute the count is handed over on. The name is what it shipped as
while this was FTP-only; it is kept so nothing that reads it has to change, and
callers use these functions rather than the name anyway."""


def reset_values_saved(station_link):
    """Open a file's counting window: from here, count from zero."""
    setattr(station_link, VALUES_SAVED_ATTR, 0)


def add_values_saved(station_link, count):
    """
    Add what core just saved to the open window, if there is one.

    Called from a plugin's ``after_save_records``. Outside a window — core
    saving records that did not come from a staged file — there is nothing to
    add to and nothing happens.
    """
    if hasattr(station_link, VALUES_SAVED_ATTR):
        setattr(station_link, VALUES_SAVED_ATTR, getattr(station_link, VALUES_SAVED_ATTR) + count)


def read_values_saved(station_link):
    """What the open window has counted so far."""
    return getattr(station_link, VALUES_SAVED_ATTR, 0)


def _aware_observation_time(record, tz):
    """
    The record's ``observation_time`` as an aware datetime, or ``None`` when
    it is missing or not a datetime (core rejects those records itself).

    Naive values are read in ``tz`` — the station link's timezone — which is
    how core interprets them too, so a mark computed here compares correctly
    with the run's ``start_date``.
    """
    value = record.get("observation_time") if isinstance(record, dict) else None
    if not isinstance(value, datetime):
        return None
    if dj_timezone.is_aware(value):
        return value
    return dj_timezone.make_aware(value, tz or dj_timezone.get_default_timezone())


def decode_and_stamp(data_file, decoder, station_link, task_logger=None, newer_than=None):
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
    :param newer_than: Exclusive lower bound: when set, only records whose
        ``observation_time`` is after it are yielded. Records without a usable
        ``observation_time`` are always yielded, for core to reject and log.
        The file is still decoded whole and its mark computed over every row,
        so skipping rows never moves the mark backwards.
    :type newer_than: datetime, optional
    :return: Whether the file decoded and was stamped
    :rtype: bool

    If the staged file has a ``latest_observation_time`` attribute, it is
    stamped with the newest observation decoded from the file, together with
    ``processed_at`` — the file's high-water mark for the next run. Staged
    files without the attribute are stamped as before.
    """
    log = task_logger or logger
    file_name = getattr(data_file, "file_name", None) or os.path.basename(data_file.file.path)

    try:
        data = decoder.decode(data_file.file.path)
        file_records = data.get("values", [])
    except SoftTimeLimitExceeded:
        # Decoding a large file is where the batch's time budget most often
        # runs out. It is an Exception subclass: swallowed here as a decode
        # failure, the run would roll on towards the hard kill.
        raise
    except Exception as e:
        log.error(f"Error decoding file {file_name}: {e}")
        return False

    log.debug(f"Decoded {len(file_records)} records from {file_name}")

    tz = getattr(station_link, "timezone", None)
    latest = None
    skipped = 0

    reset_values_saved(station_link)

    for record in file_records:
        obs_time = _aware_observation_time(record, tz)
        if obs_time is not None and (latest is None or obs_time > latest):
            latest = obs_time
        if newer_than is not None and obs_time is not None and obs_time <= newer_than:
            skipped += 1
            continue
        yield record

    if skipped:
        log.info(
            f"Skipped {skipped} of {len(file_records)} record(s) in {file_name} "
            f"at or before {newer_than} (already ingested from this file)"
        )

    if FLUSH is not None:
        # Resumes only once core has persisted this file's records
        yield FLUSH
        values_saved = read_values_saved(station_link)
    else:
        # Older core: chunks may span files, so the per-file count would
        # be attributed to whichever file happens to be current — leave
        # it unrecorded rather than record something misleading
        values_saved = None

    update_fields = ['processed_at', 'values_saved']
    data_file.processed_at = dj_timezone.now()
    data_file.values_saved = values_saved

    # A file that decoded no dated rows keeps its previous mark rather than
    # losing it
    if hasattr(data_file, "latest_observation_time") and latest is not None:
        data_file.latest_observation_time = latest
        update_fields.append('latest_observation_time')

    data_file.save(update_fields=update_fields)

    if values_saved == 0 and len(file_records) > skipped:
        log.warning(
            f"File {file_name} decoded {len(file_records)} record(s) but none of its "
            f"values were saved — check the variable mappings and the ingestion window"
        )

    return True
