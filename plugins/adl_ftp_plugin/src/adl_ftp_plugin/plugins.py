import os
import tempfile
from datetime import datetime, timedelta
from typing import Iterator, Dict, Any, Optional

from adl.core.registries import Plugin
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone as dj_timezone

try:
    # ADL >= 0.8.12: yielded between records to have core persist what is
    # buffered before the generator is resumed. On an older core the marker
    # does not exist and files are stamped without that guarantee.
    from adl.core.registries import FLUSH
except ImportError:  # pragma: no cover - older core
    FLUSH = None

from adl_ftp_plugin.date_formats import get_format_definition
from .models import FTPStationDataFile, FTPListingStrategy
from .registries import ftp_decoder_registry
from .utils import (
    normalize_path,
    get_dates_to_now,
    get_date_paths,
)


class AdlFtpPlugin(Plugin):
    type = "adl_ftp_plugin"
    label = "ADL FTP Plugin"

    def get_urls(self):
        return []

    @staticmethod
    def get_decoder(decoder_name):
        return ftp_decoder_registry.get(decoder_name)

    def _get_configured_decoder(self, network_conn_ftp):
        """
        Get and configure the decoder for the given network connection.
        Returns None if decoder is not found or misconfigured.
        """
        logger = self.get_logger()
        decoder_name = network_conn_ftp.decoder
        decoder = self.get_decoder(decoder_name)

        if not decoder:
            logger.error(f"Decoder {decoder_name} not found in decoder registry.")
            return None

        if decoder_name == "standard_csv":
            if not network_conn_ftp.csv_config:
                logger.error(f"Standard CSV decoder selected but no CSV configuration set.")
                return None
            decoder._config = network_conn_ftp.csv_config

        return decoder

    def get_default_start_date(self, station_link):
        start_date = dj_timezone.localtime(dj_timezone.now(), timezone=station_link.timezone)
        return start_date

    def get_station_data(
            self,
            station_link,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Generator that yields station records one at a time.

        This method yields records as they are decoded from FTP files,
        rather than accumulating them all in memory.

        :param station_link: The station link configuration
        :param start_date: Start date for data collection
        :param end_date: End date for data collection
        :yields: Individual observation records
        """
        logger = self.get_logger()

        network_conn_ftp = station_link.network_connection

        ftp_client = None

        try:
            ftp_client = network_conn_ftp.get_client()

            # Get and configure the decoder
            decoder = self._get_configured_decoder(network_conn_ftp)
            if not decoder:
                return

            net_ftp_name = network_conn_ftp.network.name
            station_name = station_link.station.name

            # Duck-typed sources-count handover: core stores this on the run's
            # activity log so "looked, found nothing" (0) stays distinguishable
            # from "never looked" (None). The lazy initialise-on-first-answer
            # below is the fleet-wide idiom: every failure path leaves the
            # attribute None, and core's evidence rule abstains on NULL.
            def commit_sources_count(count):
                if getattr(station_link, "adl_sources_count", None) is None:
                    station_link.adl_sources_count = 0
                station_link.adl_sources_count += count

            direct_fetch = station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH

            # Process each path — yield records as we go
            file_paths = self._get_file_paths(
                station_link, ftp_client, decoder, start_date, end_date,
                on_listing=None if direct_fetch else commit_sources_count,
            )

            for file_path in file_paths:
                current_path = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)

                logger.debug(
                    f"Getting FTP data from '{net_ftp_name}' for "
                    f"station '{station_name}' from FTP path '{current_path}'"
                )

                in_hand = yield from self._process_file(
                    station_link, current_path, file_name, decoder, ftp_client
                )

                if direct_fetch:
                    # No listing happened here: the filenames were constructed
                    # from the clock, so they are our guess and not the
                    # source's offer. What the source did answer is whether
                    # each file was there — count that instead, and count it
                    # whether or not the file went on to decode.
                    commit_sources_count(1 if in_hand else 0)

        except Exception as e:
            logger.error(f"Error fetching FTP data: {type(e).__name__}: {e}")
            raise
        finally:
            if ftp_client:
                ftp_client.close()

    def _get_file_paths(
            self,
            station_link,
            ftp_client,
            decoder,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
            on_listing=None,
    ) -> Iterator[str]:
        """
        Yields remote file paths to process based on the listing strategy.
        Does not download or decode anything.

        ``on_listing`` is called with the number of files each *successful*
        listing matched, including zero — it is how the source-items count
        leaves this method by value rather than being written here, so the
        duck-typed attribute has one assignment site in
        :meth:`get_station_data`. A listing that raises never calls it.
        """
        logger = self.get_logger()
        strategy = station_link.listing_strategy

        path = station_link.ftp_path
        timezone = station_link.timezone

        # DIRECT_FETCH knows every filename it wants, and each one names the
        # single directory it can be in — so it never enumerates directories.
        if strategy == FTPListingStrategy.DIRECT_FETCH:
            yield from self._direct_fetch_paths(station_link, start_date, end_date)
            return

        # PATTERN_ONLY / FILTER_BY_DATE discover files, so every directory in
        # the window has to be listed.
        if station_link.dir_structured_by_date and station_link.date_granularity:
            dates = get_dates_to_now(
                date_granularity=station_link.date_granularity,
                timezone=timezone,
                from_date=start_date
            )
            paths = get_date_paths(path, dates, station_link.date_granularity, station_link.month_dir_format)
        else:
            paths = [path]

        for current_path in paths:
            if not station_link.file_pattern:
                logger.error(
                    f"file_pattern is required for {strategy} strategy but is not set "
                    f"for station {station_link.station.name}. Skipping path {current_path}."
                )
                continue

            if not ftp_client.cd(current_path):
                logger.warning(f"Path {current_path} not found")
                continue

            ftp_files_list = ftp_client.list(current_path, extra=True)
            files_list = [file["name"] for file in ftp_files_list]

            if strategy == FTPListingStrategy.FILTER_BY_DATE:
                matching_files = decoder.get_matching_files(
                    station_link, files_list, start_date, end_date
                )
            else:
                matching_files = decoder.get_matching_files(
                    station_link, files_list, None, None
                )

            # The listing is in hand and parsed: this is the source's
            # answer for this directory, and an empty one is still an
            # answer.
            if on_listing is not None:
                on_listing(len(matching_files))

            if not matching_files:
                logger.debug(
                    f"No files found for station {station_link.station.name} "
                    f"matching pattern '{station_link.file_pattern}' in path {current_path}"
                )
                continue

            for file_name in matching_files:
                yield normalize_path(f"{current_path}/{file_name}")

    def _direct_fetch_paths(self, station_link, start_date, end_date):
        """
        The remote path of every file DIRECT_FETCH expects over
        ``[start_date, end_date]``, in time order.

        Under ``dir_structured_by_date`` each file's directory is derived from
        its own timestamp rather than by walking the window's directories and
        offering all of the window's filenames to each. That walk cost
        ``N_dirs x N_filenames`` RETR attempts, all but ``N_filenames`` of them
        certain misses on any backfill (wmo-raf/adl-ftp-plugin#9).

        Note the two timezones: the tree is named in the station timezone,
        while the filename carries ``direct_fetch_datetime_timezone``. Both are
        read off the same instant, so a file near a local midnight lands in the
        directory that instant belongs to.
        """
        for moment, filename in self._generate_direct_fetch_files(station_link, start_date, end_date):
            yield normalize_path(f"{station_link.date_dir_for(moment)}/{filename}")

    def _process_path(self, station_link, path: str, decoder, ftp_client, start_date=None, end_date=None):
        for file_path in self._get_file_paths(station_link, ftp_client, decoder, start_date, end_date):
            current_path = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)

            yield from self._process_file(station_link, current_path, file_name, decoder, ftp_client)

    def _generate_direct_fetch_files(self, station_link, start_date, end_date):
        """
        The expected files for a date range, as ``(moment, filename)`` pairs.

        The moment is the instant the filename encodes, kept alongside the name
        because the caller needs it to place the file in the right date
        directory — reading it back out of the name would mean parsing what we
        just formatted.

        :param station_link: The station link configuration
        :param start_date: Start datetime (UTC)
        :param end_date: End datetime (UTC)
        :return: List of (datetime, filename) pairs
        """
        import pytz

        logger = self.get_logger()

        tz = station_link.direct_fetch_datetime_timezone
        if isinstance(tz, str):
            tz = pytz.timezone(tz)

        prefix = station_link.direct_fetch_prefix
        interval = station_link.direct_fetch_interval_minutes
        extension = station_link.direct_fetch_file_extension or ".txt"
        datetime_format = station_link.direct_fetch_datetime_format

        if not datetime_format:
            logger.error(
                f"direct_fetch_datetime_format is not set for station {station_link.station.name}. "
                f"Cannot construct filenames."
            )
            return []

        format_def = get_format_definition(datetime_format)
        if not format_def:
            logger.error(f"Invalid datetime format for station {station_link.station.name}")
            return []

        # Do not generate filenames beyond now — files in the future may not exist yet
        end = min(end_date, dj_timezone.now())

        # The interval is a cadence in real time, so the walk steps in absolute
        # instants and converts each one into the filename timezone. Stepping a
        # localized datetime instead would carry the offset in force at the
        # start across any DST transition, naming an hour that does not exist
        # and — since the same instant chooses the date directory — looking for
        # the file in the wrong day.
        files = []
        moment = start_date

        while moment <= end:
            local = dj_timezone.localtime(moment, tz)
            datetime_str = local.strftime(format_def["strptime"])
            filename = f"{prefix}{datetime_str}{extension}"
            files.append((moment, filename))
            moment += timedelta(minutes=interval)

        return files

    def after_save_records(self, station_link, station_records, saved_records, qc_fail_results=None):
        """
        Per-file bookkeeping for :meth:`_process_file`.

        Core calls this after each chunk it upserts. ``_process_file`` zeroes
        the counter on the station link before yielding a file's records and
        reads it back once core has consumed the trailing ``FLUSH``, so the
        number stamped on the file is the observation values that actually
        reached the database — not the records the decoder produced.
        """
        if hasattr(station_link, "_adl_ftp_values_saved"):
            station_link._adl_ftp_values_saved += len(saved_records)

    def _process_file(self, station_link, path, file_name, decoder, ftp_client):
        """
        Generator that processes a single FTP file and yields its records.

        Downloads the file if needed, decodes it, yields each record, then
        yields core's ``FLUSH`` marker so the records are persisted before this
        generator is resumed. Only after that is the file stamped
        ``processed_at`` — "processed" means its data is in the database, not
        merely that it decoded — along with ``values_saved``, the number of
        observation values core reported upserting for it (see
        :meth:`after_save_records`).

        A file whose download or decode fails is left unstamped, so it shows as
        "Downloaded, not processed" (or not downloaded) and is retried next run.

        Returns whether the file ended up **in hand** — downloaded now, or
        already held. DIRECT_FETCH counts source items off this return, since
        no listing ever told it what the source holds. A file in hand that
        then fails to decode still counts: source items are counted before
        conversion, so our decode bug must not read as the source offering
        nothing.
        """
        logger = self.get_logger()

        # Check if file exists in database
        db_data_file = FTPStationDataFile.objects.filter(
            station_link=station_link,
            file_name=file_name
        ).first()

        # Download if new file OR re-download is enabled
        needs_download = not db_data_file or not station_link.skip_already_downloaded_files

        if needs_download:
            remote_file_path = normalize_path(f"{path}/{file_name}")

            with tempfile.NamedTemporaryFile(suffix=file_name, delete=False) as temp_file:
                temp_path = temp_file.name

                try:
                    logger.debug(f"Downloading file {file_name}..")
                    ftp_client.get(remote_file_path, temp_path)

                    # Create OR update existing record
                    if not db_data_file:
                        db_data_file = FTPStationDataFile(
                            station_link=station_link,
                            file_name=file_name,
                        )

                    with open(temp_path, 'rb') as f:
                        db_data_file.file.save(file_name, f)

                except SoftTimeLimitExceeded:
                    # It is the batch's time budget that ran out, not this file.
                    # Swallowed here (it is an Exception subclass) the run would
                    # roll on to the hard kill; propagated, core persists what
                    # earlier files yielded and records the timeout honestly.
                    raise
                except Exception as e:
                    # For direct fetch, missing files are expected — log at debug level
                    if station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
                        logger.debug(f"File {file_name} not found on server (expected for direct fetch), skipping.")
                    else:
                        logger.error(f"Error downloading file {file_name}: {e}")
                    return False

                finally:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        else:
            logger.debug(f"File {file_name} already downloaded, skipping download")

        if not db_data_file or not db_data_file.file:
            return False

        # Decode (always, whether freshly downloaded or existing)
        try:
            data = decoder.decode(db_data_file.file.path)
            file_records = data.get("values", [])
        except Exception as e:
            logger.error(f"Error decoding file {file_name}: {e}")
            return True

        logger.debug(f"Decoded {len(file_records)} records from {file_name}")

        # after_save_records adds to this as core upserts the chunks
        station_link._adl_ftp_values_saved = 0

        for record in file_records:
            yield record

        if FLUSH is not None:
            # Resumes only once core has persisted this file's records
            yield FLUSH
            values_saved = station_link._adl_ftp_values_saved
        else:
            # Older core: chunks may span files, so the per-file count would
            # be attributed to whichever file happens to be current — leave
            # it unrecorded rather than record something misleading
            values_saved = None

        db_data_file.processed_at = dj_timezone.now()
        db_data_file.values_saved = values_saved
        db_data_file.save(update_fields=['processed_at', 'values_saved'])

        if values_saved == 0:
            logger.warning(
                f"File {file_name} decoded {len(file_records)} record(s) but none of its "
                f"values were saved — check the variable mappings and the ingestion window"
            )

        return True

    def get_station_file_paths(self, station_link, start_date=None, end_date=None) -> list:
        """
        Dry run: the remote paths an ingestion run over ``[start_date,
        end_date]`` would try, in the order it would try them, without
        downloading or decoding anything. Either bound defaults to what
        :meth:`get_dates_for_station` resolves for the next real run.

        For ``DIRECT_FETCH`` the names are computed from the clock alone —
        no FTP connection is opened and no decoder is required, so the
        preview page can call this freely. Listing strategies need both.
        """
        logger = self.get_logger()
        ftp_client = None
        try:
            default_start, default_end = self.get_dates_for_station(station_link)
            start_date = start_date or default_start
            end_date = end_date or default_end

            if station_link.listing_strategy == FTPListingStrategy.DIRECT_FETCH:
                return list(self._get_file_paths(station_link, None, None, start_date, end_date))

            network_conn_ftp = station_link.network_connection
            decoder = self._get_configured_decoder(network_conn_ftp)
            if not decoder:
                return []
            ftp_client = network_conn_ftp.get_client()
            return list(self._get_file_paths(station_link, ftp_client, decoder, start_date, end_date))
        except Exception as e:
            logger.error(f"Error during dry run: {e}")
            raise
        finally:
            if ftp_client:
                ftp_client.close()
