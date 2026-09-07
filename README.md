# ADL FTP Plugin

Collects observation data from **files on an FTP, FTPS or SFTP server** — the
export directory of a data-logger network, a vendor's file drop, a partner's
SFTP account — into an [ADL](https://github.com/wmo-raf/adl) instance. On each
collection cycle ADL finds the files belonging to each linked station,
downloads the ones it does not hold, decodes them with the format decoder
chosen on the connection (Campbell TOA5, SIAP+Micros, Standard CSV, or a
decoder plugin), and stores the mapped columns as observations. The same
package provides two **dispatch channels** that push ADL observations back out
to an FTP/SFTP server as CSV.

**Operator guide:** [docs/guide.md](docs/guide.md) — prerequisites,
installation, every connection and station-link field, the decoder
configurations, the Test Decoder Configuration and Direct Fetch Files pages,
the dispatch channels, collection behaviour, diagnostics and troubleshooting.
The guide is also published on the central ADL documentation site.

## Development setup

The plugin runs inside the ADL core image. Build the `adl:latest` image from
the [ADL core repository](https://github.com/wmo-raf/adl) first, then:

```bash
git clone https://github.com/wmo-raf/adl-ftp-plugin.git
cd adl-ftp-plugin
cp .env.sample .env        # set PLUGIN_BUILD_UID=$(id -u), PLUGIN_BUILD_GID=$(id -g), ADL_DB_PASSWORD
docker compose build
docker compose up
docker compose exec adl adl createsuperuser
```

The admin is served on `PORT` (default 8080). The plugin source is
bind-mounted, so code changes reload the dev server. If the build fails with
`pull access denied` for `adl:latest`, prefix the build with
`DOCKER_BUILDKIT=0`.

Lint and format from `plugins/adl_ftp_plugin/` with `make lint` and
`make format`. See [CONTRIBUTING.md](CONTRIBUTING.md) — a change to any
connection or station-link field must update the guide in the same PR.
