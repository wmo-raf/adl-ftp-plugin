"""
Decoding a staged file, in the half of the pipeline that does not care where
the file came from.

``_process_file`` used to hold two jobs in one method: fetch the file over FTP,
then decode it, yield its records, and stamp it once core had persisted them.
Only the first half is about FTP. These tests drive the second half on a staged
file that is *not* an ``FTPStationDataFile`` — the duck type a push-based
consumer stages its uploads as (wmo-raf/adl#271) — and pin the semantics the
FTP plugin already relied on: the stamp lands after core consumed the trailing
``FLUSH``, and it carries the values core actually saved for this file alone.
"""

import os
import tempfile
from types import SimpleNamespace

from adl.core.registries import FLUSH
from django.test import SimpleTestCase

from adl_ftp_plugin.processing import add_values_saved, decode_and_stamp, reset_values_saved


class StagedFile:
    """The whole interface the shared generator is allowed to know about."""

    def __init__(self, path, file_name="upload.csv"):
        self.file = SimpleNamespace(path=path)
        self.file_name = file_name
        self.processed_at = None
        self.values_saved = None
        self.saves = []

    def save(self, update_fields=None):
        self.saves.append(tuple(update_fields or ()))


class FakeDecoder:
    def __init__(self, records=None, error=None):
        self.records = records if records is not None else []
        self.error = error
        self.decoded = []

    def decode(self, path):
        self.decoded.append(path)
        if self.error is not None:
            raise self.error
        return {"values": list(self.records)}


def drain(generator):
    """Run a generator to exhaustion: ``(what it yielded, what it returned)``."""
    yielded = []
    while True:
        try:
            yielded.append(next(generator))
        except StopIteration as stop:
            return yielded, stop.value


class DecodeAndStampTests(SimpleTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        self.addCleanup(os.unlink, self.path)
        self.staged = StagedFile(self.path)
        self.link = SimpleNamespace()
        self.record = {"observation_time": "2026-08-17T03:45:00Z", "at": 21.5}

    def test_it_decodes_the_staged_file_by_path(self):
        decoder = FakeDecoder([self.record])
        drain(decode_and_stamp(self.staged, decoder, self.link))
        self.assertEqual(decoder.decoded, [self.path])

    def test_the_stamp_lands_only_after_core_consumed_the_flush(self):
        generator = decode_and_stamp(self.staged, FakeDecoder([self.record]), self.link)

        self.assertEqual(next(generator), self.record)
        self.assertIsNone(self.staged.processed_at, "stamped before core saw the records")

        self.assertIs(next(generator), FLUSH)
        self.assertIsNone(self.staged.processed_at, "stamped before the upsert finished")

        # Core reports what it wrote, then resumes the generator
        add_values_saved(self.link, 3)
        _, decoded = drain(generator)

        self.assertTrue(decoded)
        self.assertIsNotNone(self.staged.processed_at)
        self.assertEqual(self.staged.values_saved, 3)
        self.assertEqual(self.staged.saves, [("processed_at", "values_saved")])

    def test_the_count_covers_this_file_only(self):
        reset_values_saved(self.link)
        add_values_saved(self.link, 99)  # a previous file's tally, still on the link
        generator = decode_and_stamp(self.staged, FakeDecoder([self.record]), self.link)
        next(generator)  # record
        next(generator)  # FLUSH
        add_values_saved(self.link, 1)
        drain(generator)
        self.assertEqual(self.staged.values_saved, 1)

    def test_a_file_that_decodes_to_nothing_is_stamped_with_zero(self):
        yielded, decoded = drain(decode_and_stamp(self.staged, FakeDecoder([]), self.link))
        self.assertEqual(yielded, [FLUSH])
        self.assertTrue(decoded)
        self.assertEqual(self.staged.values_saved, 0)

    def test_a_decode_failure_leaves_the_file_unstamped(self):
        yielded, decoded = drain(
            decode_and_stamp(self.staged, FakeDecoder(error=ValueError("garbage")), self.link)
        )
        self.assertEqual(yielded, [])
        self.assertFalse(decoded)
        self.assertIsNone(self.staged.processed_at)
        self.assertIsNone(self.staged.values_saved)
        self.assertEqual(self.staged.saves, [])
