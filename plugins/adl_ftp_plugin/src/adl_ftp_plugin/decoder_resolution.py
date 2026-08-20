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

Third-party decoders whose ``decode()`` predates the argument still work: they
declare no config (so none is passed), and one that does want configuration
handed to it the old way still gets it through ``_config``.
"""

import inspect
import logging
from functools import lru_cache

from .registries import ftp_decoder_registry

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _accepts_config(decode_function):
    """Does this ``decode`` implementation take a ``config`` argument?"""
    try:
        parameters = inspect.signature(decode_function).parameters.values()
    except (TypeError, ValueError):  # a callable Python cannot introspect
        return False
    return any(
        parameter.name == "config" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def decode_file(decoder, file_path, config=None):
    """
    Decode ``file_path`` with ``decoder``, handing it ``config``.

    :param decoder: A registered decoder instance
    :param file_path: Local path of the file to decode
    :param config: The configuration this decode should use, or ``None``
    :return: Whatever the decoder returns, i.e. ``{"values": [...]}``
    """
    if config is None:
        return decoder.decode(file_path)

    if _accepts_config(getattr(decoder.decode, "__func__", decoder.decode)):
        return decoder.decode(file_path, config=config)

    # A decoder written before the argument existed. The attribute is the only
    # way in, so use it — knowing it is the shared registry instance being
    # written to, which is why nothing in this plugin relies on it any more.
    decoder._config = config
    return decoder.decode(file_path)


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

    if getattr(decoder, "requires_config", False):
        if not csv_config:
            log.error(f"Decoder {decoder_name} selected but no CSV configuration set.")
            return None
        return ConfiguredDecoder(decoder, csv_config)

    return ConfiguredDecoder(decoder)


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
