"""
Tests for the "Populate variable mappings from decoder" action: the
``FTPDecoder.get_variables()`` contract, the pure helpers in
``decoder_variables.py``, the admin link on ``NetworkFTP`` and the review view.
"""

from adl.core.models import DataParameter, Network, Unit
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from adl_ftp_plugin.decoder_variables import (
    create_variable_mappings,
    find_parameter_for_variable,
    find_unit_by_symbol,
    get_decoder_variables,
    get_unmapped_decoder_variables,
    normalise_decoder_variables,
)
from adl_ftp_plugin.models import ConnectionType, FTPVariableMapping, NetworkFTP
from adl_ftp_plugin.registries import FTPDecoder, ftp_decoder_registry

STUB_TYPE = "test_stub_vars_decoder"


class StubVariablesDecoder(FTPDecoder):
    type = STUB_TYPE
    compat_type = STUB_TYPE
    display_name = "Stub decoder with variables"

    def decode(self, file_path):
        return {"values": []}

    def get_variables(self):
        return [
            {"name": "ta", "unit": "°C", "label": "Air Temperature"},
            {"name": "ws", "unit": "knot", "label": "Wind Speed", "adl_unit": "m/s"},
            {"name": "lw", "unit": "1", "label": "Leaf Wetness"},
            {"name": "wd", "unit": "degree", "label": "Wind Direction", "aggregation_method": "circular"},
        ]


class DecoderVariablesTestBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ftp_decoder_registry.register(StubVariablesDecoder())

    @classmethod
    def tearDownClass(cls):
        ftp_decoder_registry.unregister(STUB_TYPE)
        super().tearDownClass()

    def make_connection(self, decoder=STUB_TYPE, name="KMD FTP"):
        network = Network.objects.create(name=f"Network for {name}", type="automatic")
        return NetworkFTP.objects.create(
            name=name,
            network=network,
            plugin="adl_ftp_plugin",
            connection_type=ConnectionType.FTP,
            host="ftp.example.test",
            username="user",
            password="secret",
            decoder=decoder,
        )


class GetVariablesContractTests(DecoderVariablesTestBase):
    def test_base_decoder_declares_no_variables(self):
        class Bare(FTPDecoder):
            type = "bare"

        self.assertEqual(Bare().get_variables(), [])

    def test_normalise_applies_defaults_and_drops_bad_entries(self):
        result = normalise_decoder_variables([
            {"name": "ta", "unit": "°C"},
            {"name": "ws", "unit": "knot", "label": "Wind Speed", "adl_unit": "m/s"},
            {"unit": "mm"},  # no name
            {"name": "x"},  # no unit
            {"name": "ta", "unit": "K"},  # duplicate name, first wins
            "garbage",
        ])
        self.assertEqual(
            result,
            [
                {"name": "ta", "unit": "°C", "label": "ta", "adl_unit": "°C"},
                {"name": "ws", "unit": "knot", "label": "Wind Speed", "adl_unit": "m/s"},
            ],
        )

    def test_get_decoder_variables_for_declaring_decoder(self):
        conn = self.make_connection()
        names = [v["name"] for v in get_decoder_variables(conn)]
        self.assertEqual(names, ["ta", "ws", "lw", "wd"])

    def test_get_decoder_variables_empty_for_builtin_and_unknown_decoders(self):
        self.assertEqual(get_decoder_variables(self.make_connection(decoder="toa5", name="toa5")), [])
        self.assertEqual(get_decoder_variables(self.make_connection(decoder="does_not_exist", name="nope")), [])

    def test_unmapped_excludes_existing_file_variable_names(self):
        conn = self.make_connection()
        unit = Unit.objects.create(name="Degree Celsius", symbol="°C")
        param = DataParameter.objects.create(name="Air Temperature", unit=unit)
        FTPVariableMapping.objects.create(
            network_ftp=conn, adl_parameter=param, file_variable_name="ta", file_variable_unit=unit
        )
        names = [v["name"] for v in get_unmapped_decoder_variables(conn)]
        self.assertEqual(names, ["ws", "lw", "wd"])


class LookupHelperTests(DecoderVariablesTestBase):
    def test_find_unit_by_symbol_exact_then_pint_equivalent(self):
        self.assertIsNone(find_unit_by_symbol("°C"))
        degc = Unit.objects.create(name="Celsius", symbol="degC")
        # pint treats degC and °C as the same unit
        self.assertEqual(find_unit_by_symbol("°C"), degc)
        exact = Unit.objects.create(name="Degree Celsius", symbol="°C")
        self.assertEqual(find_unit_by_symbol("°C"), exact)

    def test_find_parameter_matches_label_or_name_case_insensitively(self):
        unit = Unit.objects.create(name="Degree Celsius", symbol="°C")
        param = DataParameter.objects.create(name="air temperature", unit=unit)
        variable = {"name": "ta", "unit": "°C", "label": "Air Temperature", "adl_unit": "°C"}
        self.assertEqual(find_parameter_for_variable(variable), param)
        by_name = DataParameter.objects.create(name="TA", unit=unit)
        variable = {"name": "ta", "unit": "°C", "label": "Something Else", "adl_unit": "°C"}
        self.assertEqual(find_parameter_for_variable(variable), by_name)


class CreateVariableMappingsTests(DecoderVariablesTestBase):
    def test_creates_units_parameters_and_mappings(self):
        conn = self.make_connection()
        variables = get_decoder_variables(conn)
        rows = [{"variable": v, "file_variable_unit": None, "adl_parameter": None} for v in variables]

        summary = create_variable_mappings(conn, rows)

        self.assertEqual(summary["created"], 4)
        self.assertEqual(summary["skipped_existing"], 0)
        # core migration 0041 ships the dimensionless "1" unit: reused, not created
        self.assertEqual(sorted(summary["units_created"]), sorted(["°C", "knot", "m/s", "degree"]))
        self.assertEqual(
            sorted(summary["parameters_created"]),
            ["Air Temperature", "Leaf Wetness", "Wind Direction", "Wind Speed"],
        )

        ws = FTPVariableMapping.objects.get(network_ftp=conn, file_variable_name="ws")
        self.assertEqual(ws.file_variable_unit.symbol, "knot")
        self.assertEqual(ws.adl_parameter.name, "Wind Speed")
        self.assertEqual(ws.adl_parameter.unit.symbol, "m/s")

        wd = DataParameter.objects.get(name="Wind Direction")
        self.assertEqual(wd.aggregation_method, "circular")

        knot = Unit.objects.get(symbol="knot")
        self.assertEqual(knot.name, "knot")  # pint spelling
        self.assertEqual(Unit.objects.get(symbol="°C").name, "Degree Celsius")  # core predefined name

        # sort_order is contiguous
        orders = list(
            FTPVariableMapping.objects.filter(network_ftp=conn).order_by("sort_order")
            .values_list("sort_order", flat=True)
        )
        self.assertEqual(orders, [0, 1, 2, 3])

    def test_reuses_selected_existing_objects_and_skips_existing_mappings(self):
        conn = self.make_connection()
        degc = Unit.objects.create(name="Celsius", symbol="degC")
        param = DataParameter.objects.create(name="Temp", unit=degc)
        variables = {v["name"]: v for v in get_decoder_variables(conn)}

        create_variable_mappings(conn, [
            {"variable": variables["ta"], "file_variable_unit": degc, "adl_parameter": param},
        ])
        ta = FTPVariableMapping.objects.get(network_ftp=conn, file_variable_name="ta")
        self.assertEqual(ta.file_variable_unit, degc)
        self.assertEqual(ta.adl_parameter, param)
        self.assertFalse(Unit.objects.filter(symbol="°C").exists())

        summary = create_variable_mappings(conn, [
            {"variable": variables["ta"], "file_variable_unit": None, "adl_parameter": None},
            {"variable": variables["lw"], "file_variable_unit": None, "adl_parameter": None},
        ])
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped_existing"], 1)
        self.assertEqual(FTPVariableMapping.objects.filter(network_ftp=conn, file_variable_name="ta").count(), 1)

    def test_same_named_parameter_is_reused_not_duplicated(self):
        conn = self.make_connection()
        ms = Unit.objects.create(name="Meters per Second", symbol="m/s")
        existing = DataParameter.objects.create(name="wind speed", unit=ms)
        variables = {v["name"]: v for v in get_decoder_variables(conn)}

        create_variable_mappings(conn, [
            {"variable": variables["ws"], "file_variable_unit": None, "adl_parameter": None},
        ])
        self.assertEqual(DataParameter.objects.filter(name__iexact="wind speed").count(), 1)
        ws = FTPVariableMapping.objects.get(network_ftp=conn, file_variable_name="ws")
        self.assertEqual(ws.adl_parameter, existing)


class AdminLinkTests(DecoderVariablesTestBase):
    LABEL = "Populate Variable Mappings from Decoder"

    def labels(self, conn):
        return [str(link["label"]) for link in conn.get_extra_model_admin_links()]

    def test_link_shown_when_decoder_declares_variables(self):
        conn = self.make_connection()
        self.assertIn(self.LABEL, self.labels(conn))
        link = [x for x in conn.get_extra_model_admin_links() if str(x["label"]) == self.LABEL][0]
        self.assertEqual(link["url"], reverse("populate_variable_mappings_from_decoder", args=[conn.id]))

    def test_link_hidden_for_decoder_without_variables(self):
        self.assertNotIn(self.LABEL, self.labels(self.make_connection(decoder="toa5", name="toa5")))

    def test_link_survives_unknown_decoder(self):
        conn = self.make_connection(decoder="does_not_exist", name="nope")
        self.assertNotIn(self.LABEL, self.labels(conn))


class PopulateViewTests(DecoderVariablesTestBase):
    def setUp(self):
        self.conn = self.make_connection()
        self.url = reverse("populate_variable_mappings_from_decoder", args=[self.conn.id])
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.test", "pw")
        self.client.force_login(self.admin)

    @staticmethod
    def formset_data(rows):
        """rows: dicts with keys name/include/file_variable_unit/adl_parameter."""
        data = {
            "form-TOTAL_FORMS": str(len(rows)),
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            data[f"form-{i}-name"] = row["name"]
            if row.get("include", True):
                data[f"form-{i}-include"] = "on"
            data[f"form-{i}-file_variable_unit"] = str(row.get("file_variable_unit") or "")
            data[f"form-{i}-adl_parameter"] = str(row.get("adl_parameter") or "")
        return data

    def test_get_renders_one_row_per_unmapped_variable_with_prefill(self):
        degc = Unit.objects.create(name="Degree Celsius", symbol="°C")
        param = DataParameter.objects.create(name="air temperature", unit=degc)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        formset = response.context["formset"]
        self.assertEqual(len(formset.forms), 4)
        self.assertEqual(formset.forms[0].initial["file_variable_unit"], degc)
        self.assertEqual(formset.forms[0].initial["adl_parameter"], param)
        self.assertIsNone(formset.forms[1].initial["file_variable_unit"])
        self.assertIsNone(formset.forms[1].initial["adl_parameter"])
        self.assertIn("knot", str(formset.forms[1].fields["file_variable_unit"].empty_label))
        self.assertIn("Wind Speed", str(formset.forms[1].fields["adl_parameter"].empty_label))
        self.assertContains(response, "Declared: knot")

    def test_post_creates_units_parameters_and_mappings(self):
        data = self.formset_data([{"name": n} for n in ("ta", "ws", "lw", "wd")])

        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.conn.edit_url, fetch_redirect_response=False)
        self.assertEqual(FTPVariableMapping.objects.filter(network_ftp=self.conn).count(), 4)
        for symbol in ("°C", "knot", "m/s", "1", "degree"):
            self.assertTrue(Unit.objects.filter(symbol=symbol).exists(), symbol)
        self.assertEqual(DataParameter.objects.get(name="Wind Speed").unit.symbol, "m/s")
        self.assertEqual(DataParameter.objects.get(name="Leaf Wetness").unit.symbol, "1")

    def test_post_respects_include_and_selected_existing_objects(self):
        degc = Unit.objects.create(name="Celsius", symbol="degC")
        param = DataParameter.objects.create(name="Temp", unit=degc)
        data = self.formset_data([
            {"name": "ta", "file_variable_unit": degc.id, "adl_parameter": param.id},
            {"name": "ws", "include": False},
            {"name": "lw"},
            {"name": "wd", "include": False},
        ])

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        names = set(FTPVariableMapping.objects.filter(network_ftp=self.conn).values_list("file_variable_name", flat=True))
        self.assertEqual(names, {"ta", "lw"})
        ta = FTPVariableMapping.objects.get(network_ftp=self.conn, file_variable_name="ta")
        self.assertEqual(ta.adl_parameter, param)
        self.assertEqual(ta.file_variable_unit, degc)
        self.assertFalse(Unit.objects.filter(symbol="knot").exists())

    def test_post_rejects_incompatible_parameter_unit(self):
        pct = Unit.objects.create(name="Percent", symbol="%")
        rh = DataParameter.objects.create(name="Relative Humidity", unit=pct)
        data = self.formset_data([{"name": "ws", "adl_parameter": rh.id}])  # knot -> %

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FTPVariableMapping.objects.filter(network_ftp=self.conn).exists())
        self.assertTrue(response.context["formset"].forms[0].errors.get("adl_parameter"))

    def test_rerun_is_idempotent(self):
        self.client.post(self.url, self.formset_data([{"name": n} for n in ("ta", "ws", "lw", "wd")]))

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["formset"].forms), 0)
        self.assertContains(response, "already mapped")

        self.client.post(self.url, self.formset_data([{"name": "ta"}]))
        self.assertEqual(FTPVariableMapping.objects.filter(network_ftp=self.conn).count(), 4)

    def test_decoder_without_variables_redirects_with_warning(self):
        conn = self.make_connection(decoder="toa5", name="toa5")
        url = reverse("populate_variable_mappings_from_decoder", args=[conn.id])
        response = self.client.get(url)
        self.assertRedirects(response, conn.edit_url, fetch_redirect_response=False)

    def test_permission_denied_without_change_permission(self):
        user = get_user_model().objects.create_user("plain", "plain@example.test", "pw")
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(FTPVariableMapping.objects.filter(network_ftp=self.conn).exists())

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
