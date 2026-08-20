"""
Where do pre-populated variable mappings get written?

The "Populate variable mappings from decoder" helpers were generic over the
ORM except for their last step, which created ``FTPVariableMapping`` rows keyed
by ``network_ftp``. A second consumer with its own mapping model could use
every helper but that one. These tests pin the parametrised target
(wmo-raf/adl#271), using the plugin's *other* mapping model — the per-station
one, keyed by ``station_link`` — as the stand-in for it.
"""

from adl.core.models import DataParameter, Network, Unit
from adl.core.tests.factories import StationFactory
from django.test import TestCase

from adl_ftp_plugin.decoder_variables import create_variable_mappings
from adl_ftp_plugin.models import (
    ConnectionType,
    FTPListingStrategy,
    FTPStationLink,
    FTPStationLinkVariableMapping,
    FTPVariableMapping,
    NetworkFTP,
)


def variable(name, unit="°C", label=None):
    return {"name": name, "unit": unit, "label": label or name, "adl_unit": unit}


class MappingTargetTests(TestCase):
    def setUp(self):
        self.network = Network.objects.create(name="KMD", type="automatic")
        self.connection = NetworkFTP.objects.create(
            name="KMD FTP",
            network=self.network,
            plugin="adl_ftp_plugin",
            connection_type=ConnectionType.FTP,
            host="ftp.example.test",
            username="user",
            password="secret",
            decoder="toa5",
        )
        self.link = FTPStationLink.objects.create(
            network_connection=self.connection,
            station=StationFactory(network=self.network),
            ftp_path="/data",
            file_pattern="*.dat",
            listing_strategy=FTPListingStrategy.PATTERN_ONLY,
        )
        self.unit = Unit.objects.create(name="Degree Celsius", symbol="°C")
        self.parameter = DataParameter.objects.create(name="Air Temperature", unit=self.unit)

    def row(self, name):
        return {
            "variable": variable(name),
            "file_variable_unit": self.unit,
            "adl_parameter": self.parameter,
        }

    def station_link_mappings(self):
        return FTPStationLinkVariableMapping.objects.filter(station_link=self.link)

    def test_rows_land_on_the_model_and_field_asked_for(self):
        summary = create_variable_mappings(
            self.link,
            [self.row("ta")],
            mapping_model=FTPStationLinkVariableMapping,
            connection_field="station_link",
        )

        self.assertEqual(summary["created"], 1)
        mapping = self.station_link_mappings().get()
        self.assertEqual(mapping.file_variable_name, "ta")
        self.assertEqual(mapping.adl_parameter, self.parameter)
        self.assertEqual(mapping.file_variable_unit, self.unit)
        self.assertFalse(FTPVariableMapping.objects.exists(), "wrote to the connection model too")

    def test_already_mapped_variables_are_skipped_on_that_model(self):
        FTPStationLinkVariableMapping.objects.create(
            station_link=self.link,
            adl_parameter=self.parameter,
            file_variable_name="ta",
            file_variable_unit=self.unit,
            sort_order=4,
        )

        summary = create_variable_mappings(
            self.link,
            [self.row("ta"), self.row("rh")],
            mapping_model=FTPStationLinkVariableMapping,
            connection_field="station_link",
        )

        self.assertEqual((summary["created"], summary["skipped_existing"]), (1, 1))
        self.assertEqual(
            list(self.station_link_mappings().order_by("sort_order").values_list(
                "file_variable_name", "sort_order")),
            [("ta", 4), ("rh", 5)],
            "the new row did not continue that model's ordering",
        )

    def test_the_connection_mapping_stays_the_default(self):
        summary = create_variable_mappings(self.connection, [self.row("ta")])

        self.assertEqual(summary["created"], 1)
        self.assertEqual(
            FTPVariableMapping.objects.get(network_ftp=self.connection).file_variable_name, "ta"
        )
        self.assertFalse(self.station_link_mappings().exists())
