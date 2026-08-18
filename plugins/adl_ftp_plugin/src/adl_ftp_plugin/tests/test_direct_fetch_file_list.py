"""
The direct-fetch file list: a preview page, reached from the station link,
of the remote paths the next ingestion run would try under ``DIRECT_FETCH``
plus whether ADL already holds each file. Pure computation — the page and
the plugin dry-run it calls must never open an FTP/SFTP connection.
"""
import time
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from adl.core.tests.factories import StationFactory
from django.contrib.auth import get_user_model
from django.core.cache import cache
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

    def test_filename_summary_renders_the_configured_pattern(self):
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T10:10"})
        self.assertContains(response, "STATION1_&lt;YYYYMMDDHHMM&gt;.txt")
        self.assertNotContains(response, "{{")
        self.assertNotContains(response, "{%")

    def test_empty_window_message_renders_translated(self):
        # A window entirely in the future generates no filenames (nothing is
        # generated beyond now) → the empty-state block shows
        response = self._get(**{"from": "2099-01-01T10:00", "to": "2099-01-01T10:10"})
        self.assertContains(response, "No filenames fall in this window")
        self.assertNotContains(response, "{%")

    def test_local_status_distinguishes_processed_files_that_saved_nothing(self):
        # A green "Processed" next to a file whose values were all dropped is
        # exactly what hid the bug this page now exists to expose
        FTPStationDataFile.objects.create(
            station_link=self.link, file_name="STATION1_202608171000.txt",
            processed_at=dj_timezone.now(), values_saved=0,
        )
        FTPStationDataFile.objects.create(
            station_link=self.link, file_name="STATION1_202608171010.txt",
            processed_at=dj_timezone.now(), values_saved=25,
        )
        response = self._get(**{"from": "2026-08-17T10:00", "to": "2026-08-17T10:10"})
        self.assertContains(response, "Processed, 0 values saved")
        self.assertContains(response, "25 values")

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


# ---------------------------------------------------------------------------
# Per-row remote check
# ---------------------------------------------------------------------------

class FakeStatClient:
    """A stub client answering stat_file() from a canned table."""

    def __init__(self, table=None, error=None):
        self.table = table or {}
        self.error = error
        self.asked = []
        self.closed = False

    def stat_file(self, path):
        self.asked.append(path)
        if self.error is not None:
            raise self.error
        return self.table.get(path, {"exists": False, "size": None})

    def close(self):
        self.closed = True


class OwnPathGuardTests(SimpleTestCase):

    def setUp(self):
        from adl_ftp_plugin.views import _is_own_direct_fetch_path
        self.guard = _is_own_direct_fetch_path
        self.link = make_station_link(**DIRECT_FETCH_KWARGS)

    def test_accepts_a_path_the_link_would_generate(self):
        self.assertTrue(self.guard(self.link, "/data/station1/STATION1_202608171000.txt"))

    def test_accepts_a_date_structured_subdirectory(self):
        self.assertTrue(self.guard(self.link, "/data/station1/2026/08/17/STATION1_202608171000.txt"))

    def test_rejects_paths_outside_the_base(self):
        for path in (
            "/etc/STATION1_202608171000.txt",
            "/data/station10/STATION1_202608171000.txt",
            "/data/station1/../secret/STATION1_202608171000.txt",
            "STATION1_202608171000.txt",
            "",
        ):
            self.assertFalse(self.guard(self.link, path), path)

    def test_rejects_names_of_another_shape(self):
        for path in (
            "/data/station1/OTHER_202608171000.txt",
            "/data/station1/STATION1_202608171000.csv",
            "/data/station1/STATION1_notadate.txt",
        ):
            self.assertFalse(self.guard(self.link, path), path)


class ClientStatFileTests(SimpleTestCase):

    def _ftp_client(self, conn):
        from adl_ftp_plugin.ftp import FTPClient
        client = FTPClient.__new__(FTPClient)
        client.conn = conn
        return client

    def test_ftp_reports_size_for_an_existing_file(self):
        conn = mock.Mock()
        conn.size.return_value = 1234
        self.assertEqual(self._ftp_client(conn).stat_file("/d/f.txt"), {"exists": True, "size": 1234})
        conn.voidcmd.assert_called_once_with("TYPE I")

    def test_ftp_550_means_not_found(self):
        from ftplib import error_perm
        conn = mock.Mock()
        conn.size.side_effect = error_perm("550 /d/f.txt: No such file or directory")
        self.assertEqual(self._ftp_client(conn).stat_file("/d/f.txt"), {"exists": False, "size": None})

    def test_ftp_other_failures_raise(self):
        from ftplib import error_perm
        from adl_ftp_plugin.ftp import FTPError
        conn = mock.Mock()
        conn.size.side_effect = error_perm("530 Not logged in")
        with self.assertRaises(FTPError):
            self._ftp_client(conn).stat_file("/d/f.txt")

    def _sftp_client(self, sftp):
        from adl_ftp_plugin.ftp.sftp import SFTPClient
        client = SFTPClient.__new__(SFTPClient)
        client.sftp = sftp
        return client

    def test_sftp_reports_size_for_an_existing_file(self):
        sftp = mock.Mock()
        sftp.stat.return_value = mock.Mock(st_size=99)
        self.assertEqual(self._sftp_client(sftp).stat_file("/d/f.txt"), {"exists": True, "size": 99})

    def test_sftp_missing_file_is_a_normal_answer(self):
        sftp = mock.Mock()
        sftp.stat.side_effect = FileNotFoundError(2, "No such file")
        self.assertEqual(self._sftp_client(sftp).stat_file("/d/f.txt"), {"exists": False, "size": None})

    def test_sftp_other_failures_raise(self):
        from adl_ftp_plugin.ftp.sftp import SFTPError
        sftp = mock.Mock()
        sftp.stat.side_effect = OSError(13, "Permission denied")
        with self.assertRaises(SFTPError):
            self._sftp_client(sftp).stat_file("/d/f.txt")


class DirectFetchFileCheckViewTests(TestCase):

    PATH = "/data/station1/STATION1_202608171000.txt"

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
        self.url = reverse("ftp_direct_fetch_file_check", args=[self.link.pk])
        self.list_url = reverse("ftp_direct_fetch_file_list", args=[self.link.pk])

    def _post(self, fake, path=PATH):
        with mock.patch.object(NetworkFTP, "get_client", return_value=fake):
            return self.client.post(self.url, {"path": path})

    def test_existing_file_reports_size(self):
        fake = FakeStatClient({self.PATH: {"exists": True, "size": 512}})
        response = self._post(fake)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"path": self.PATH, "exists": True, "size": 512})
        self.assertEqual(fake.asked, [self.PATH])
        self.assertTrue(fake.closed)

    def test_missing_file_is_a_normal_answer(self):
        response = self._post(FakeStatClient())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["exists"], False)

    def test_connection_failure_is_reported_as_502(self):
        from adl_ftp_plugin.ftp import FTPError
        response = self._post(FakeStatClient(error=FTPError("Unable to reach FTP host", 502)))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "Unable to reach FTP host")

    def test_foreign_path_is_refused_before_any_connection(self):
        fake = FakeStatClient()
        response = self._post(fake, path="/etc/passwd")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.asked, [])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_non_direct_fetch_link_is_refused(self):
        self.link.listing_strategy = FTPListingStrategy.PATTERN_ONLY
        self.link.file_pattern = "STATION1_*.txt"
        self.link.save()
        self.assertEqual(self._post(FakeStatClient()).status_code, 400)

    def test_viewer_cannot_probe_and_sees_no_button(self):
        from django.contrib.auth.models import Permission
        viewer = get_user_model().objects.create_user("viewer", "v@example.test", "pw")
        viewer.user_permissions.add(
            Permission.objects.get(codename="access_admin"),
            Permission.objects.get(codename="view_ftpstationlink"),
        )
        self.client.force_login(viewer)
        page = self.client.get(self.list_url, {"from": "2026-08-17T10:00", "to": "2026-08-17T10:00"})
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["can_check_remote"])
        self.assertNotContains(page, "data-path=")
        self.assertNotContains(page, 'id="dff-check-form"')
        self.assertIn(self._post(FakeStatClient()).status_code, (302, 403))

    def test_manager_sees_the_button_on_the_list(self):
        page = self.client.get(self.list_url, {"from": "2026-08-17T10:00", "to": "2026-08-17T10:00"})
        self.assertTrue(page.context["can_check_remote"])
        self.assertContains(page, 'data-path="/data/station1/STATION1_202608171000.txt"')
        self.assertContains(page, self.url)


# ---------------------------------------------------------------------------
# Whole-page remote sweep
# ---------------------------------------------------------------------------

class SlowStatClient(FakeStatClient):
    """A stub client whose every stat_file() takes ``delay`` seconds."""

    def __init__(self, delay, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay

    def stat_file(self, path):
        time.sleep(self.delay)
        return super().stat_file(path)


class DirectFetchCheckPageViewTests(TestCase):
    """
    One press, one connection, every path on the page — the sweep the per-row
    button cannot give without 200 handshakes. The paths are recomputed from
    the same window the page was rendered with, never taken from the client.
    """

    def setUp(self):
        cache.clear()
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
        self.url = reverse("ftp_direct_fetch_file_check_page", args=[self.link.pk])
        self.list_url = reverse("ftp_direct_fetch_file_list", args=[self.link.pk])

    def _post(self, fake, **params):
        params.setdefault("from", "2026-08-17T10:00")
        params.setdefault("to", "2026-08-17T10:20")
        counting = mock.Mock(return_value=fake)
        with mock.patch.object(NetworkFTP, "get_client", counting):
            response = self.client.post(self.url, params)
        self.connects = counting.call_count
        return response

    def test_sweeps_the_whole_page_over_one_connection(self):
        first = "/data/station1/STATION1_202608171000.txt"
        fake = FakeStatClient({first: {"exists": True, "size": 512}})
        response = self._post(fake)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["partial"])
        self.assertEqual([r["path"] for r in body["results"]], [
            first,
            "/data/station1/STATION1_202608171010.txt",
            "/data/station1/STATION1_202608171020.txt",
        ])
        self.assertEqual(body["results"][0], {"path": first, "exists": True, "size": 512})
        self.assertEqual(body["results"][1], {"path": body["results"][1]["path"],
                                              "exists": False, "size": None})
        self.assertEqual(self.connects, 1)
        self.assertEqual(len(fake.asked), 3)
        self.assertTrue(fake.closed)

    def test_a_failing_path_is_reported_and_the_sweep_continues(self):
        from adl_ftp_plugin.ftp import FTPError

        class OneBadPath(FakeStatClient):
            def stat_file(self, path):
                if path.endswith("1010.txt"):
                    raise FTPError("530 Not logged in", 502)
                return super().stat_file(path)

        fake = OneBadPath()
        response = self._post(fake)
        body = response.json()
        self.assertEqual(len(body["results"]), 3)
        self.assertEqual(body["results"][1]["error"], "530 Not logged in")
        self.assertNotIn("exists", body["results"][1])
        self.assertEqual(len(fake.asked), 2)  # the bad path raises before it records
        self.assertTrue(fake.closed)

    def test_only_the_requested_page_is_swept(self):
        # 10-minute files over 34 hours = 205 paths, so page 2 holds five
        response = self._post(FakeStatClient(), **{
            "from": "2026-08-17T00:00", "to": "2026-08-18T10:00", "p": "2",
        })
        body = response.json()
        self.assertEqual(len(body["results"]), 5)
        self.assertEqual(body["results"][0]["path"],
                         "/data/station1/STATION1_202608180920.txt")

    def test_a_slow_host_returns_what_was_answered_and_says_it_is_partial(self):
        from adl_ftp_plugin import views

        fake = SlowStatClient(0.05)
        with mock.patch.object(views, "CHECK_PAGE_WALL_CLOCK_SECONDS", 0.08):
            response = self._post(fake, **{"from": "2026-08-17T10:00", "to": "2026-08-17T11:00"})
        body = response.json()
        self.assertTrue(body["partial"])
        self.assertLess(len(body["results"]), 7)
        self.assertTrue(fake.closed)

    def test_a_dead_host_is_reported_as_502(self):
        from adl_ftp_plugin.ftp import FTPError
        counting = mock.Mock(side_effect=FTPError("Unable to reach FTP host", 502))
        with mock.patch.object(NetworkFTP, "get_client", counting):
            response = self.client.post(self.url, {"from": "2026-08-17T10:00",
                                                   "to": "2026-08-17T10:20"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "Unable to reach FTP host")

    def test_a_repeat_sweep_of_the_same_page_is_held_off(self):
        self.assertEqual(self._post(FakeStatClient()).status_code, 200)
        second = self._post(FakeStatClient())
        self.assertEqual(second.status_code, 429)
        self.assertTrue(second.json()["error"])
        self.assertEqual(self.connects, 0)
        # A different page is a different target, so it is not held off
        other = self._post(FakeStatClient(), **{"from": "2026-08-17T00:00",
                                                "to": "2026-08-18T10:00", "p": "2"})
        self.assertEqual(other.status_code, 200)

    def test_an_unreadable_window_is_refused_before_any_connection(self):
        response = self._post(FakeStatClient(), **{"from": "not-a-date"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.connects, 0)

    def test_a_window_with_no_files_answers_without_connecting(self):
        with mock.patch.object(AdlFtpPlugin, "get_station_file_paths", return_value=[]):
            response = self._post(FakeStatClient())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": [], "partial": False})
        self.assertEqual(self.connects, 0)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_non_direct_fetch_link_is_refused(self):
        self.link.listing_strategy = FTPListingStrategy.PATTERN_ONLY
        self.link.file_pattern = "STATION1_*.txt"
        self.link.save()
        self.assertEqual(self._post(FakeStatClient()).status_code, 400)

    def test_viewer_cannot_sweep_and_sees_no_button(self):
        from django.contrib.auth.models import Permission
        viewer = get_user_model().objects.create_user("viewer", "v@example.test", "pw")
        viewer.user_permissions.add(
            Permission.objects.get(codename="access_admin"),
            Permission.objects.get(codename="view_ftpstationlink"),
        )
        self.client.force_login(viewer)
        page = self.client.get(self.list_url, {"from": "2026-08-17T10:00", "to": "2026-08-17T10:20"})
        self.assertNotContains(page, "dff-check-page")
        self.assertIn(self._post(FakeStatClient()).status_code, (302, 403))
        self.assertEqual(self.connects, 0)

    def test_manager_sees_the_page_button(self):
        page = self.client.get(self.list_url, {"from": "2026-08-17T10:00", "to": "2026-08-17T10:20"})
        self.assertContains(page, "dff-check-page")
        self.assertContains(page, self.url)


class TemplateTagsDoNotSpanLinesTests(SimpleTestCase):
    """Django's template lexer matches ``{{ … }}`` / ``{% … %}`` on a single
    line only; a tag broken across lines by a formatter is emitted as literal
    text (the ``{{`` shows up in the page). This guards every plugin template."""

    def test_no_tag_is_split_across_lines(self):
        import pathlib
        template_dir = pathlib.Path(__file__).resolve().parent.parent / "templates"
        offenders = []
        for path in sorted(template_dir.rglob("*.html")):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for opener, closer in (("{{", "}}"), ("{%", "%}")):
                    idx = line.rfind(opener)
                    if idx != -1 and closer not in line[idx:]:
                        offenders.append(f"{path.relative_to(template_dir)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))
