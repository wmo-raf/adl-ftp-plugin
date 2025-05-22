# ADL FTP Plugin

Generic plugin for connecting to FTP servers, downloading and decoding AWS data files and saving to ADL database.

## Getting started

### Prerequisites

- Docker and Docker Compose installed on your machine.
- Git installed on your machine.

### Install and build the ADL Core Image

The ADL FTP plugin is a module intended to be installed in an [ADL](https://github.com/wmo-raf/adl) instance. This means
that you need to first get the core ADL system and build it on your local development environment.

You can follow the instructions on the [ADL core repository](https://github.com/wmo-raf/adl) to install and build the
ADL core image

### Install the ADL FTP plugin

The `dev.Dockerfile` file uses the `adl` image as a base image. The `ADL FTP plugin` is installed during the build
process. Using docker mounted volumes, the plugin is editable such that any changes made to the code trigger Django to
reload the development server, allowing you to see the changes as you develop

1. Clone the plugin repository:

```bash
git clone https://github.com/wmo-raf/adl-ftp-plugin.git
cd adl-ftp-plugin
```

2. Create a `.env` file using the provided `.env.sample` file:

```bash
cp .env.sample .env
```

3. Edit the `.env` file to set the required environment variables

```bash
nano .env
```

You can use the default values provided in the `.env.sample` file, but be sure to set the following correctly:

- `PLUGIN_BUILD_UID`: The UID of the user that will run the plugin inside the container
- `PLUGIN_BUILD_GID`: The GID of the user that will run the plugin inside the container

You can find the UID and GID of your user by running the following command:

```bash
id -u
id -g
```

4. Build the plugin image:

```bash
docker compose build
```

If you are getting errors like
`failed to solve: adl:latest: failed to resolve source metadata for docker.io/library/adl:latest: pull access denied`,
you might need to disable `DOCKER_BUILDKIT` when building the image.

You can do this by running the following

```bash
DOCKER_BUILDKIT=0  docker compose build
```

5. Start the plugin:

```bash
docker compose up
```

If everything is set up correctly, you should see the plugin starting up and listening for incoming requests. You can
access the plugin at `http://localhost:8000`. The port number can be changed using the `PORT` environment variable in
the `.env`. The default port is `8000`.

6. Create superuser

```bash
docker compose exec adl adl createsuperuser
```

The `adl`command is shorthand for `python manage.py` command. You can use it to run any Django management command
inside the container.

## Concepts

To use this plugin, you need to understand a few concepts relating to observation data collection, using FTP as a
cache/storage mechanism.

- FTP data collection
- Data formats
- Decoding the data to a standardized format

### FTP data collection

Most AWS vendors, provide a way to push observation data for individual stations to an FTP server, once collected. This
could be directly from the station data logger to an FTP server, or from the vendor's data collection system to an FTP
server. Despite the method, this process is usually automated and the data is pushed to the FTP server regularly,
depending on the collection settings.

To be able to use this plugin, you need an FTP server with the correct credentials.

It is also important to understand the directory structure of the FTP server. For example:

- Do you have a folder for each station or do you place all the files in a single folder?
- Is the station directory structured by year/month/day or is it flat?
- Is data written to a new file every time or is it appended to an existing file?

This understanding is important because the plugin will need to know how to navigate the FTP server to find the data
files. For each station, you will need to configure the plugin with the correct path to the data files.

### Data formats and decoding

Different AWS vendors provide data in different formats. The files are usually text files with different file extensions
names like `.txt`, `.csv`, `.dat`, etc. These data files might be encoded and structured for transmission, and in this
case, you will need to understand how to decode this data to get the actual observation data in an understandable
format.

The plugin provides a way to decode the data files using a decoder. The decoder is basically a python class that
implements the `decode` method. This method takes the data file as input and returns the decoded data in a standardized
format.

The plugin provides a few inbuilt decoders for different data formats. These include:

- `Toa5Decoder` for decoding files in the TOA5 format, a format mostly used by Campbell Scientific data loggers.
- `SiapMicrosDecoder` for decoding files from Siap+Micros data loggers

You can also create your own decoder and use it with the plugin.



