"""
Which decoder, holding which configuration?

A decoder lives in the registry as a single shared instance: one object per
decoder type for the whole process. Configuration, though, belongs to the
connection — two connections can use the standard CSV decoder with different
delimiters and datetime columns. The plugin used to bridge that by writing the
connection's config onto the registry's decoder just before decoding, so the
answer to "which config is in force?" depended on who last ran. Two station
links in one worker, or a second plugin sharing this registry, could read each
other's configuration (wmo-raf/adl#271).

Here the config is a value that travels with the call instead: resolution
returns a :class:`ConfiguredDecoder` — one connection's decoder-and-config pair
— and decoding passes the config as an argument. Nothing is written back onto
the registry.

Third-party decoders whose ``decode()`` predates the argument are unaffected:
they declare no config, so none is passed and they are called exactly as
before.
"""

import logging

from .registries import ftp_decoder_registry

logger = logging.getLogger(__name__)


def decode_file(decoder, file_path, config=None):
    """
    Decode ``file_path`` with ``decoder``, handing it ``config``.

    A decoder is only ever handed a config it asked for by declaring
    ``requires_config``, and declaring that is what commits it to accepting the
    argument.

    :param decoder: A registered decoder instance
    :param file_path: Local path of the file to decode
    :param config: The configuration this decode should use, or ``None``
    :return: Whatever the decoder returns, i.e. ``{"values": [...]}``
    """
    if config is None:
        return decoder.decode(file_path)

    return decoder.decode(file_path, config=config)


class ConfiguredDecoder:
    """
    A decoder bound to the configuration one connection wants it to use.

    Answers ``decode(file_path)`` — the whole interface the ingestion pipeline
    needs — and delegates everything else (``display_name``, ``pre_process``,
    ``get_matching_files``, ...) to the decoder itself.
    """

    def __init__(self, decoder, config=None):
        self.decoder = decoder
        self.config = config

    def decode(self, file_path):
        return decode_file(self.decoder, file_path, self.config)

    def __getattr__(self, name):
        try:
            decoder = self.__dict__["decoder"]
        except KeyError:  # pragma: no cover - only before __init__ ran
            raise AttributeError(name)
        return getattr(decoder, name)

    def __repr__(self):
        return f"<ConfiguredDecoder {self.decoder!r} config={self.config!r}>"


def decoder_requires_config(decoder_name):
    """
    Does the decoder registered under ``decoder_name`` need the connection's
    ``csv_config``? ``False`` for a name that is not registered — the caller
    cannot ask a decoder it does not have.

    This is the one place the rule lives: resolution refuses a connection that
    breaks it, and ``NetworkFTP.clean()`` stops the connection being saved that
    way in the first place.
    """
    try:
        decoder = ftp_decoder_registry.get(decoder_name)
    except Exception:  # unknown decoder name, plugin not installed, ...
        return False
    return bool(getattr(decoder, "requires_config", False))


def resolve_decoder(decoder_name, csv_config=None, task_logger=None):
    """
    The registered decoder for ``decoder_name``, bound to ``csv_config`` if it
    declares that it needs one.

    :param decoder_name: Registry key, e.g. what a connection stores in ``decoder``
    :param csv_config: The connection's ``StandardCSVConfig``, if it has one
    :param task_logger: Where to report a misconfiguration; defaults to this
        module's logger. Plugins pass their :class:`~adl.core.logging.TaskLogger`
        so the message lands on the run's activity log.
    :return: A :class:`ConfiguredDecoder`, or ``None`` when the decoder is
        missing or is missing the configuration it needs
    :raises InstanceTypeDoesNotExist: If ``decoder_name`` is not registered
    """
    log = task_logger or logger
    decoder = ftp_decoder_registry.get(decoder_name)

    if not decoder:
        log.error(f"Decoder {decoder_name} not found in decoder registry.")
        return None

    if not getattr(decoder, "requires_config", False):
        return ConfiguredDecoder(decoder)

    if not csv_config:
        log.error(f"Decoder {decoder_name} selected but no CSV configuration set.")
        return None

    return ConfiguredDecoder(decoder, csv_config)


def resolve_decoder_for_connection(connection, task_logger=None):
    """
    :func:`resolve_decoder` for a connection, read off the two fields every
    file-based connection carries: ``decoder`` and ``csv_config``. Duck-typed,
    so a connection model outside this plugin resolves the same way.
    """
    return resolve_decoder(
        connection.decoder,
        csv_config=getattr(connection, "csv_config", None),
        task_logger=task_logger,
    )
