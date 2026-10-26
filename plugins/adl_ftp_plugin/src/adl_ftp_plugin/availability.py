"""
The tier-1 availability check for an FTP station link: does the server have
files for this window? Answered from a stat or a directory listing, never a
download.

Support is decided from ``listing_strategy`` alone (see
:func:`availability_check_supported`), so core can word its button without
dialling anything. ``PATTERN_ONLY`` filenames carry no time, and a file's
modification time does not prove what it covers, so that strategy is left to
core's fetch-and-parse tier.

Filenames are never trusted beyond what is parsed: a file that exists is one
range at the granularity its name implies, with no record count. Recovery
reports what actually saved.
"""

import fnmatch
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from adl.core.availability_checks import AvailabilityCheckResult, AvailabilityRange
from adl.core.source_checks import SourceCheckStatus
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext

from .date_formats import get_format_definition
from .file_matching import parse_date_from_filename
from .ftp import FTPError
from .ftp.sftp import SFTPError
from .utils import normalize_path

# Strictly inside core's 15 s probe wall clock, so a window with too many
# files to stat reports itself instead of being cut off by the executor.
TIME_BUDGET_SECONDS = 10


def availability_check_supported(station_link):
    """True for the strategies that can answer from names alone. I/O-free."""
    from .models import FTPListingStrategy
    return station_link.listing_strategy in (
        FTPListingStrategy.DIRECT_FETCH,
        FTPListingStrategy.FILTER_BY_DATE,
    )


def check_availability(station_link, start, end):
    from .models import FTPListingStrategy, failed_source_check_result

    if not availability_check_supported(station_link):
        return AvailabilityCheckResult(
            status=SourceCheckStatus.UNSUPPORTED,
            message=gettext(
                "Filenames of a Pattern Only link carry no time, so the source "
                "cannot be checked from a file listing."
            ),
        )

    try:
        client = station_link.network_connection.get_client()
    except (FTPError, SFTPError) as e:
        return _failed(failed_source_check_result(e))

    try:
        if station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
            return _check_direct_fetch(station_link, client, start, end)
        return _check_filter_by_date(station_link, client, start, end)
    except (FTPError, SFTPError) as e:
        return _failed(failed_source_check_result(e))
    finally:
        client.close()


def _failed(source_check_result):
    return AvailabilityCheckResult(
        status=source_check_result.status,
        category=source_check_result.category,
        message=source_check_result.message,
    )


def _timed_out(paths_done, paths_total):
    return AvailabilityCheckResult(
        status=SourceCheckStatus.FAILED,
        category="TCP_TIMEOUT",
        message=gettext(
            "Stopped after checking %(done)s of %(total)s files within the time "
            "limit. Try by fetching data instead."
        ) % {"done": paths_done, "total": paths_total},
    )


def _check_direct_fetch(station_link, client, start, end):
    from .plugins import AdlFtpPlugin

    # The same names a recovery over this window would try. The generator
    # includes its end instant, but the window is half-open.
    expected = [
        (moment, filename)
        for moment, filename in AdlFtpPlugin()._generate_direct_fetch_files(station_link, start, end)
        if moment < end
    ]
    interval = timedelta(minutes=station_link.direct_fetch_interval_minutes)

    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    ranges = []
    directories = []
    for index, (moment, filename) in enumerate(expected):
        if time.monotonic() > deadline:
            return _timed_out(index, len(expected))
        directory = station_link.date_dir_for(moment)
        if directory not in directories:
            directories.append(directory)
        # One SIZE / stat round-trip: existence without a transfer.
        if client.stat_file(normalize_path(f"{directory}/{filename}"))["exists"]:
            ranges.append(AvailabilityRange(moment, moment + interval, None, filename))

    return AvailabilityCheckResult(
        status=SourceCheckStatus.OK,
        ranges=tuple(ranges),
        message=gettext(
            "Looked for %(expected)s file(s) in %(paths)s: %(found)s found."
        ) % {
            "expected": len(expected),
            "paths": ", ".join(directories) or station_link.ftp_path,
            "found": len(ranges),
        },
    )


def _window_directories(station_link, start, end):
    """The directories the window's files can be in, in time order. Steps by
    the hour, the finest a directory tree is carved."""
    directories = []
    moment = start
    while moment < end:
        directory = station_link.date_dir_for(moment)
        if directory not in directories:
            directories.append(directory)
        moment += timedelta(hours=1)
    return directories or [station_link.date_dir_for(start)]


def _file_range(filename, date_format, tz):
    """The range a dated filename implies, or ``None`` when its date does not
    parse. A date-only name covers its local day; a time name covers one unit
    of its finest field."""
    parsed = parse_date_from_filename(filename, date_format, tz)
    if parsed is None:
        return None
    strptime = (get_format_definition(date_format) or {}).get("strptime") or ""
    if "%S" in strptime or not strptime:
        return parsed, parsed + timedelta(seconds=1)
    if "%M" in strptime:
        return parsed, parsed + timedelta(minutes=1)
    if "%H" in strptime:
        return parsed, parsed + timedelta(hours=1)
    # Local midnight to the next local midnight, so a DST day is 23 or 25 h.
    day = parsed.date()
    naive_next = datetime.combine(day + timedelta(days=1), datetime.min.time())
    return parsed, dj_timezone.make_aware(naive_next, tz)


def _check_filter_by_date(station_link, client, start, end):
    tz = station_link.filename_date_timezone
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    date_structured = bool(station_link.dir_structured_by_date and station_link.date_granularity)
    pattern = station_link.file_pattern or "*"

    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    directories = _window_directories(station_link, start, end)
    ranges = []
    missing = []
    for index, directory in enumerate(directories):
        if time.monotonic() > deadline:
            return _timed_out(index, len(directories))
        if not client.cd(directory):
            if not date_structured:
                return AvailabilityCheckResult(
                    status=SourceCheckStatus.FAILED,
                    category="PATH_NOT_FOUND",
                    message=gettext(
                        "Resolved remote path %(path)s was not found on the server."
                    ) % {"path": directory},
                )
            # A date directory that does not exist yet is an answer: no files.
            missing.append(directory)
            continue
        names = [entry["name"] for entry in client.list(directory, extra=True)]
        for name in fnmatch.filter(names, pattern):
            file_range = _file_range(name, station_link.filename_date_format, tz)
            if file_range is None:
                continue
            file_start, file_end = file_range
            if file_end > start and file_start < end:
                ranges.append(AvailabilityRange(file_start, file_end, None, name))

    ranges.sort(key=lambda r: r.start)
    message = gettext(
        "Listed %(paths)s: %(found)s file(s) matching '%(pattern)s' fall in the window."
    ) % {"paths": ", ".join(directories), "found": len(ranges), "pattern": pattern}
    if missing:
        message += " " + gettext("Not found on the server: %(paths)s.") % {"paths": ", ".join(missing)}
    return AvailabilityCheckResult(
        status=SourceCheckStatus.OK,
        ranges=tuple(ranges),
        message=message,
    )
