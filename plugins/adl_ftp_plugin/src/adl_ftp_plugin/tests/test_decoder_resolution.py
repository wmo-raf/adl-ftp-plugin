"""
Which decoder does a connection use, and how does its configuration get there?

The FTP plugin used to answer the second question by writing the connection's
CSV configuration onto the decoder *held in the registry* — one object shared
by every connection in the process. Two station links decoding in the same
worker could therefore read each other's configuration, and a second consumer
of the registry (the agent plugin) would join the same race.

These tests pin the replacement (wmo-raf/adl#271): resolution hands back a
decoder bound to one connection's configuration, the configuration reaches
``decode()`` as an argument, and the registry's decoder is left untouched.
"""

import os
import tempfile
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from adl_ftp_plugin.decoder_resolution import (
    ConfiguredDecoder,
    decode_file,
    decoder_requires_config,
    resolve_decoder,
    resolve_decoder_for_connection,
)
from adl_ftp_plugin.decoders.standard_csv import StandardCSVDecoder
from adl_ftp_plugin.models import ConnectionType, NetworkFTP, StandardCSVConfig
from adl_ftp_plugin.plugins import AdlFtpPlugin
from adl_ftp_plugin.registries import FTPDecoder, ftp_decoder_registry

CONFIG_AWARE_TYPE = "test_config_aware_decoder"
LEGACY_TYPE = "test_legacy_decoder"


class ConfigAwareDecoder(FTPDecoder):
    """Records the config each call was handed, so cross-talk is visible."""

    type = CONFIG_AWARE_TYPE
    compat_type = CONFIG_AWARE_TYPE
    display_name = "Config aware decoder"
    requires_config = True

    def decode(self, file_path, config=None):
        return {"values": [{"config": config, "file_path": file_path}]}


class LegacyDecoder(FTPDecoder):
    """A third-party decoder written before ``decode()`` took a config."""

    type = LEGACY_TYPE
    compat_type = LEGACY_TYPE
    display_name = "Legacy decoder"

    def decode(self, file_path):
        return {"values": [{"file_path": file_path}]}


def csv_config(name, **kwargs):
    """An unsaved CSV configuration — the decoder only reads its fields."""
    kwargs.setdefault("datetime_column", "TIMESTAMP")
    kwargs.setdefault("datetime_format", "%Y-%m-%d %H:%M:%S")
    return StandardCSVConfig(name=name, **kwargs)


def make_connection(decoder, config=None):
    return NetworkFTP(
        name=f"conn-{decoder}",
        plugin="adl_ftp_plugin",
        connection_type=ConnectionType.FTP,
        host="ftp.example.test",
        username="user",
        password="secret",
        decoder=decoder,
        csv_config=config,
    )


class DecoderResolutionTestBase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ftp_decoder_registry.register(ConfigAwareDecoder())
        ftp_decoder_registry.register(LegacyDecoder())

    @classmethod
    def tearDownClass(cls):
        ftp_decoder_registry.unregister(CONFIG_AWARE_TYPE)
        ftp_decoder_registry.unregister(LEGACY_TYPE)
        super().tearDownClass()


class ConfigTravelsPerCallTests(DecoderResolutionTestBase):
    def test_two_connections_decode_with_their_own_config(self):
        one = csv_config("one")
        two = csv_config("two")

        first = resolve_decoder(CONFIG_AWARE_TYPE, csv_config=one)
        second = resolve_decoder(CONFIG_AWARE_TYPE, csv_config=two)

        # Interleaved, the way two station links in one worker would run
        first_before = first.decode("/tmp/a.csv")["values"][0]["config"]
        second_result = second.decode("/tmp/b.csv")["values"][0]["config"]
        first_after = first.decode("/tmp/c.csv")["values"][0]["config"]

        self.assertIs(first_before, one)
        self.assertIs(second_result, two)
        self.assertIs(first_after, one, "the second connection's config leaked into the first")

    def test_resolving_leaves_the_registered_decoder_alone(self):
        registered = ftp_decoder_registry.get(CONFIG_AWARE_TYPE)
        resolve_decoder(CONFIG_AWARE_TYPE, csv_config=csv_config("one")).decode("/tmp/a.csv")
        self.assertIsNone(
            getattr(registered, "_config", None),
            "config was written onto the shared registry instance",
        )

    def test_a_decoder_that_takes_no_config_still_decodes(self):
        configured = resolve_decoder(LEGACY_TYPE)
        self.assertEqual(configured.decode("/tmp/a.csv"), {"values": [{"file_path": "/tmp/a.csv"}]})

    def test_decode_file_passes_nothing_when_there_is_no_config(self):
        self.assertEqual(
            decode_file(ConfigAwareDecoder(), "/tmp/a.csv")["values"][0]["config"], None
        )


class ConfiguredDecoderTests(DecoderResolutionTestBase):
    def test_it_delegates_everything_it_does_not_answer_itself(self):
        decoder = ConfigAwareDecoder()
        configured = ConfiguredDecoder(decoder, config=None)

        self.assertEqual(configured.display_name, "Config aware decoder")
        self.assertEqual(configured.type, CONFIG_AWARE_TYPE)
        self.assertIs(configured.decoder, decoder)

    def test_it_delegates_file_matching(self):
        configured = ConfiguredDecoder(ConfigAwareDecoder(), config=None)
        station_link = SimpleNamespace(
            file_pattern="STATION1_*.dat",
            listing_strategy="pattern_only",
            filename_date_format=None,
            filename_date_timezone=None,
        )
        self.assertEqual(
            configured.get_matching_files(station_link, ["STATION1_1.dat", "other.txt"]),
            ["STATION1_1.dat"],
        )


class RequiresConfigRuleTests(DecoderResolutionTestBase):
    """One rule, asked of the decoder — so the connection form and an
    ingestion run cannot disagree about what needs configuring."""

    def test_a_decoder_says_for_itself_whether_it_needs_a_config(self):
        self.assertTrue(decoder_requires_config("standard_csv"))
        self.assertTrue(decoder_requires_config(CONFIG_AWARE_TYPE))
        self.assertFalse(decoder_requires_config(LEGACY_TYPE))

    def test_an_unregistered_decoder_needs_nothing(self):
        self.assertFalse(decoder_requires_config("does_not_exist"))


class ConnectionValidationTests(DecoderResolutionTestBase):
    """The admin refuses what a run would refuse, off the same rule."""

    def test_a_connection_is_invalid_without_the_config_its_decoder_needs(self):
        with self.assertRaises(ValidationError) as raised:
            make_connection(CONFIG_AWARE_TYPE).clean()
        self.assertIn("csv_config", raised.exception.message_dict)

    def test_a_decoder_needing_no_config_validates_without_one(self):
        make_connection(LEGACY_TYPE).clean()  # does not raise

    def test_the_csv_decoder_still_demands_its_config(self):
        with self.assertRaises(ValidationError) as raised:
            make_connection("standard_csv").clean()
        self.assertIn("csv_config", raised.exception.message_dict)


class ResolveForConnectionTests(DecoderResolutionTestBase):
    def test_standard_csv_is_bound_to_the_connections_config(self):
        config = csv_config("kmd")
        configured = resolve_decoder_for_connection(make_connection("standard_csv", config))
        self.assertIsInstance(configured.decoder, StandardCSVDecoder)
        self.assertIs(configured.config, config)

    def test_standard_csv_without_a_config_is_refused(self):
        self.assertIsNone(resolve_decoder_for_connection(make_connection("standard_csv")))

    def test_a_decoder_that_declares_no_config_is_handed_none(self):
        connection = make_connection(LEGACY_TYPE, csv_config("ignored"))
        self.assertIsNone(resolve_decoder_for_connection(connection).config)

    def test_any_decoder_declaring_a_config_is_refused_without_one(self):
        self.assertIsNone(resolve_decoder_for_connection(make_connection(CONFIG_AWARE_TYPE)))

    def test_the_plugin_resolves_through_the_same_helper(self):
        config = csv_config("kmd")
        connection = make_connection("standard_csv", config)
        configured = AdlFtpPlugin()._get_configured_decoder(connection)
        self.assertIs(configured.config, config)

    def test_the_plugin_refuses_standard_csv_without_a_config(self):
        self.assertIsNone(AdlFtpPlugin()._get_configured_decoder(make_connection("standard_csv")))


class StandardCSVDecoderConfigArgumentTests(SimpleTestCase):
    """The real CSV decoder, decoding with a config it was never assigned."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(handle, "w") as f:
            f.write("TIMESTAMP,at\n2026-08-17 03:45:00,21.5\n")
        self.addCleanup(os.unlink, self.path)

    def test_decode_uses_the_config_argument(self):
        decoder = StandardCSVDecoder()
        values = decoder.decode(self.path, config=csv_config("passed-in"))["values"]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["at"], 21.5)
        self.assertFalse(hasattr(decoder, "_config"), "decode assigned config to the instance")

    def test_decode_without_any_config_is_still_an_error(self):
        with self.assertRaises(ValueError):
            StandardCSVDecoder().decode(self.path)
