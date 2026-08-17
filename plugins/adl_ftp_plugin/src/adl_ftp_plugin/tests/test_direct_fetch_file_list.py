"""
The direct-fetch file list: a preview page, reached from the station link,
of the remote paths the next ingestion run would try under ``DIRECT_FETCH``
plus whether ADL already holds each file. Pure computation — the page and
the plugin dry-run it calls must never open an FTP/SFTP connection.
"""
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from adl.core.tests.factories import StationFactory
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from adl_ftp_plugin.models import (
    FTPListingStrategy,
    FTPStationDataFile,
    FTPStationLink,
    NetworkFTP,
)
from adl_ftp_plugin.plugins import AdlFtpPlugin
from adl_ftp_plugin.tests.test_source_checks import make_connection, make_station_link

DIRECT_FETCH_KWARGS = {
    "ftp_path": "/data/station1",
    "listing_strategy": FTPListingStrategy.DIRECT_FETCH,
    "direct_fetch_prefix": "STATION1_",
    "direct_fetch_datetime_format": "YYYYMMDDHHMM",
    "direct_fetch_interval_minutes": 10,
    "direct_fetch_datetime_timezone": "UTC",
    "direct_fetch_file_extension": ".txt",
}

UTC = dt_timezone.utc


class ExplodingConnection:
    """Stands in for get_client(): any attempt to connect fails the test."""

    def __call__(self):
        raise AssertionError("the direct-fetch preview must not open an FTP connection")


class ExtraAdminLinksTests(SimpleTestCase):

    def test_direct_fetch_link_offers_the_file_list(self):
        link = make_station_link(**DIRECT_FETCH_KWARGS)
        link.pk = 7
        links = link.get_extra_model_admin_links()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], reverse("ftp_direct_fetch_file_list", args=[7]))
        self.assertTrue(links[0]["label"])

    def test_listing_strategies_offer_nothing(self):
        for strategy in (FTPListingStrategy.PATTERN_ONLY, FTPListingStrategy.FILTER_BY_DATE):
            link = make_station_link(listing_strategy=strategy)
            link.pk = 7
            self.assertEqual(link.get_extra_model_admin_links(), [], strategy)

    def test_unsaved_link_offers_nothing(self):
        # No pk → no URL to build; the add form must not blow up on the hook
        self.assertEqual(make_station_link(**DIRECT_FETCH_KWARGS).get_extra_model_admin_links(), [])


class DryRunDirectFetchTests(SimpleTestCase):

    def setUp(self):
        self.plugin = AdlFtpPlugin()
        self.link = make_station_link(**DIRECT_FETCH_KWARGS)
        self.link.network_connection.get_client = ExplodingConnection()

    def test_explicit_window_needs_no_connection_and_no_decoder(self):
        start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 17, 10, 30, tzinfo=UTC)
        with mock.patch.object(self.plugin, "get_dates_for_station",
                               return_value=(start, end)) as resolve:
            paths = self.plugin.get_station_file_paths(self.link, start, end)
        resolve.assert_called_once()  # defaults still resolved; explicit bounds win
        self.assertEqual(paths, [
            "/data/station1/STATION1_202608171000.txt",
            "/data/station1/STATION1_202608171010.txt",
            "/data/station1/STATION1_202608171020.txt",
            "/data/station1/STATION1_202608171030.txt",
        ])

    def test_missing_bounds_default_to_the_next_run_window(self):
        start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
        end = datetime(2026, 8, 17, 10, 10, tzinfo=UTC)
        with mock.patch.object(self.plugin, "get_dates_for_station", return_value=(start, end)):
            paths = self.plugin.get_station_file_paths(self.link)
        self.assertEqual(len(paths), 2)


class DirectFetchFileListViewTests(TestCase):

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.test", "pw")
        self.client.force_login(self.admin)
        self.connection = make_connection(
            plugin="adl_ftp_plugin", name="Direct FTP", username="u", password="p"
        )
        self.connection.network = StationFactory().network
        self.connection.save()
        self.link = FTPStationLink.objects.create(
            network_connection=self.connection,
            station=StationFactory(network=self.connection.network),
            **DIRECT_FETCH_KWARGS,
        )
        self.url = reverse("ftp_direct_fetch_file_list", args=[self.link.pk])

    def _get(self, **params):
        with mock.patch.object(NetworkFTP, "get_client", ExplodingConnection()):
            return self.client.get(self.url, params)

    def test_lists_the_generated_paths_for_an_explicit_window(self):
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T10:20"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/data/station1/STATION1_202608171000.txt")
        self.assertContains(response, "/data/station1/STATION1_202608171020.txt")
        self.assertNotContains(response, "STATION1_202608171030.txt")
        self.assertEqual(response.context["total_files"], 3)
        self.assertEqual(response.context["total_directories"], 1)
        self.assertTrue(response.context["window_overridden"])

    def test_default_window_is_the_next_run_window(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["window_overridden"])
        # No observations and no start_date → the default start rule is named
        self.assertIn("now", str(response.context["start_source"]))

    def test_local_status_marks_downloaded_and_processed_files(self):
        FTPStationDataFile.objects.create(
            station_link=self.link, file_name="STATION1_202608171000.txt",
            processed_at=dj_timezone.now(),
        )
        FTPStationDataFile.objects.create(
            station_link=self.link, file_name="STATION1_202608171010.txt",
        )
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T10:20"})
        rows = {r["file_name"]: r for r in response.context["rows"]}
        self.assertIsNotNone(rows["STATION1_202608171000.txt"]["local_file"].processed_at)
        self.assertIsNone(rows["STATION1_202608171010.txt"]["local_file"].processed_at)
        self.assertIsNone(rows["STATION1_202608171020.txt"]["local_file"])
        self.assertContains(response, "Processed")
        self.assertContains(response, "Downloaded, not processed")
        self.assertContains(response, "Not downloaded")

    def test_filename_datetime_is_decoded_per_row(self):
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T10:00"})
        row = response.context["rows"][0]
        self.assertEqual(row["file_datetime"], datetime(2026, 8, 17, 10, 0, tzinfo=UTC))

    def test_pagination_keeps_the_total_and_slices_rows(self):
        # 10-minute files over 3 days = 433 names → 3 pages of 200
        response = self._get(**{"from": "2026-08-14T00:00", "to": "2026-08-17T00:00", "p": "3"})
        self.assertEqual(response.context["total_files"], 433)
        self.assertEqual(len(response.context["rows"]), 33)
        self.assertEqual(response.context["rows"][0]["index"], 401)

    def test_unreadable_bound_is_reported_not_swallowed(self):
        response = self._get(**{"from": "yesterday"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["errors"])
        self.assertEqual(response.context["rows"], [])

    def test_inverted_window_is_reported(self):
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T09:00"})
        self.assertTrue(response.context["errors"])
        self.assertEqual(response.context["total_files"], 0)

    def test_non_direct_fetch_link_gets_a_notice_and_no_list(self):
        self.link.listing_strategy = FTPListingStrategy.PATTERN_ONLY
        self.link.file_pattern = "STATION1_*.txt"
        self.link.save()
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_direct_fetch"])
        self.assertContains(response, "only applies to station links")
        self.assertNotIn("rows", response.context)

    def test_view_permission_is_enough(self):
        from django.contrib.auth.models import Permission
        viewer = get_user_model().objects.create_user("viewer", "v@example.test", "pw")
        viewer.user_permissions.add(
            Permission.objects.get(codename="access_admin"),
            Permission.objects.get(codename="view_ftpstationlink"),
        )
        self.client.force_login(viewer)
        self.assertEqual(self._get().status_code, 200)

    def test_user_without_station_link_permission_is_refused(self):
        from django.contrib.auth.models import Permission
        # Admin access but no station-link permission of any kind
        plain = get_user_model().objects.create_user("plain", "plain@example.test", "pw")
        plain.user_permissions.add(Permission.objects.get(codename="access_admin"))
        self.client.force_login(plain)
        # Wagtail's admin wrapper turns PermissionDenied into a redirect to
        # the admin home with a message; either way the page is not rendered
        response = self._get()
        self.assertIn(response.status_code, (302, 403))
        self.assertNotContains(response, "STATION1_", status_code=response.status_code)

    def test_never_opens_a_connection(self):
        # ExplodingConnection is patched in for every request above; make the
        # invariant explicit for the default-window path as well
        with mock.patch.object(NetworkFTP, "get_client", ExplodingConnection()):
            self.assertEqual(self.client.get(self.url).status_code, 200)
