"""
Helpers behind the "Populate variable mappings from decoder" admin action.

A decoder may declare the variables it emits via ``FTPDecoder.get_variables()``
(see ``registries.py`` for the entry schema). These helpers normalise that
declaration, work out which variables a connection has not mapped yet, resolve
or create the ``Unit`` / ``DataParameter`` rows each one needs, and finally
create the mapping rows themselves — by default the connection-level
``FTPVariableMapping``, but any model of the same shape (see
``create_variable_mappings``).

Everything here is plain Python over the ORM so the view stays thin and the
behaviour is unit-testable without HTTP.
"""

import logging

from django.db import transaction
from django.db.models import Max

logger = logging.getLogger(__name__)

# Optional keys a decoder may set on a variable entry that are passed through
# verbatim when a DataParameter is auto-created for it.
_PARAMETER_PASSTHROUGH_KEYS = ("aggregation_method", "custom_unit_context", "description")


def normalise_decoder_variables(raw):
    """
    Apply defaults to a raw ``get_variables()`` result and drop unusable entries.

    - ``label`` defaults to ``name``; ``adl_unit`` defaults to ``unit``.
    - Entries missing ``name`` or ``unit`` are dropped.
    - Duplicate names keep the first occurrence.
    """
    seen = set()
    variables = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        unit = entry.get("unit")
        if not name or not unit:
            continue
        name = str(name)
        if name in seen:
            continue
        seen.add(name)

        variable = {
            "name": name,
            "unit": str(unit),
            "label": str(entry.get("label") or name),
            "adl_unit": str(entry.get("adl_unit") or unit),
        }
        for key in _PARAMETER_PASSTHROUGH_KEYS:
            if entry.get(key):
                variable[key] = entry[key]
        variables.append(variable)
    return variables


def get_decoder_variables(connection):
    """
    Variables declared by the connection's decoder, normalised.

    Returns ``[]`` when the decoder is not installed/registered or does not
    declare variables — callers use this to decide whether to show the action.
    """
    try:
        decoder = connection.get_decoder()
    except Exception:  # unknown decoder name, plugin not installed, ...
        return []
    if decoder is None:
        return []
    getter = getattr(decoder, "get_variables", None)
    if not callable(getter):
        return []
    try:
        return normalise_decoder_variables(getter())
    except Exception:
        logger.exception("Decoder %r failed in get_variables()", connection.decoder)
        return []


def get_unmapped_decoder_variables(connection, variables=None):
    """Declared variables whose ``name`` has no connection-level mapping yet."""
    if variables is None:
        variables = get_decoder_variables(connection)
    mapped = set(connection.variable_mappings.values_list("file_variable_name", flat=True))
    return [v for v in variables if v["name"] not in mapped]


def find_unit_by_symbol(symbol):
    """
    Existing ``Unit`` for a pint symbol: exact symbol match first, then any
    unit whose symbol pint considers the same unit (``degC`` for ``°C``).
    """
    from adl.core.models import Unit

    unit = Unit.objects.filter(symbol=symbol).first()
    if unit:
        return unit

    try:
        from adl.core.units import units
        target = units(symbol).u
    except Exception:
        return None

    for candidate in Unit.objects.all():
        try:
            if units(candidate.symbol).u == target:
                return candidate
        except Exception:
            continue
    return None


def find_parameter_for_variable(variable):
    """Existing ``DataParameter`` named like the label or name (case-insensitive)."""
    from adl.core.models import DataParameter

    for candidate in (variable.get("label"), variable.get("name")):
        if not candidate:
            continue
        parameter = DataParameter.objects.filter(name__iexact=candidate).first()
        if parameter:
            return parameter
    return None


def default_unit_name(symbol):
    """
    A human name for a unit ADL is about to create for ``symbol``.

    Prefers the names core uses for its predefined units, then pint's own
    spelling (``knot``, ``dimensionless``, ``watt / meter ** 2``), then the
    symbol itself. Falls back to the symbol if the chosen name is already taken.
    """
    from adl.core.models import Unit

    name = None
    try:
        from adl.core.constants import PREDEFINED_DATA_PARAMETERS
        for parameter in PREDEFINED_DATA_PARAMETERS:
            for key in ("unit", "wis2box_aws_csv_template_unit"):
                unit = parameter.get(key) or {}
                if unit.get("symbol") == symbol:
                    name = str(unit.get("name"))
                    break
            if name:
                break
    except Exception:
        name = None

    if not name:
        try:
            from adl.core.units import units
            name = str(units(symbol).u)
        except Exception:
            name = symbol

    if Unit.objects.filter(name=name).exclude(symbol=symbol).exists():
        name = symbol
    return name


def get_or_create_unit(symbol):
    """``(Unit, created)`` for a pint symbol; ``full_clean`` validates the symbol."""
    from adl.core.models import Unit

    unit = Unit.objects.filter(symbol=symbol).first()
    if unit:
        return unit, False
    unit = Unit(name=default_unit_name(symbol), symbol=symbol)
    unit.full_clean()
    unit.save()
    return unit, True


def get_or_create_parameter(variable, unit):
    """
    ``(DataParameter, created)`` for a variable's label, using ``unit`` as the
    canonical unit when creating. Reuses a same-named parameter if one exists
    (the name is unique) rather than failing.
    """
    from adl.core.models import DataParameter

    label = variable["label"]
    parameter = DataParameter.objects.filter(name__iexact=label).first()
    if parameter:
        return parameter, False

    fields = {"name": label, "unit": unit}
    for key in _PARAMETER_PASSTHROUGH_KEYS:
        if variable.get(key):
            fields[key] = variable[key]
    parameter = DataParameter(**fields)
    parameter.full_clean()
    parameter.save()
    return parameter, True


def create_variable_mappings(connection, rows, mapping_model=None, connection_field="network_ftp"):
    """
    Create variable-mapping rows owned by ``connection``.

    ``rows`` is a list of dicts::

        {
            "variable": <normalised variable dict>,
            "file_variable_unit": <Unit or None -> create from variable["unit"]>,
            "adl_parameter": <DataParameter or None -> create from label/adl_unit>,
        }

    Defaults to the FTP plugin's connection-level ``FTPVariableMapping``. A
    consumer with its own mapping model — another plugin, or this plugin's
    per-station mappings — passes ``mapping_model`` and the name of the field
    pointing back at ``connection``; the model needs the same three fields
    (``adl_parameter``, ``file_variable_name``, ``file_variable_unit``) and
    ``Orderable``'s ``sort_order``.

    Runs in one transaction. Variables already mapped on that model for this
    owner are skipped, so re-running is safe. Returns a summary dict.
    """
    if mapping_model is None:
        from .models import FTPVariableMapping
        mapping_model = FTPVariableMapping

    owned = mapping_model.objects.filter(**{connection_field: connection})

    summary = {
        "created": 0,
        "skipped_existing": 0,
        "units_created": [],
        "parameters_created": [],
    }

    with transaction.atomic():
        existing_names = set(owned.values_list("file_variable_name", flat=True))
        next_order = owned.aggregate(m=Max("sort_order"))["m"]
        next_order = 0 if next_order is None else next_order + 1

        for row in rows:
            variable = row["variable"]
            name = variable["name"]
            if name in existing_names:
                summary["skipped_existing"] += 1
                continue

            file_unit = row.get("file_variable_unit")
            if file_unit is None:
                file_unit, created = get_or_create_unit(variable["unit"])
                if created:
                    summary["units_created"].append(file_unit.symbol)

            parameter = row.get("adl_parameter")
            if parameter is None:
                adl_unit, created = get_or_create_unit(variable["adl_unit"])
                if created:
                    summary["units_created"].append(adl_unit.symbol)
                parameter, created = get_or_create_parameter(variable, adl_unit)
                if created:
                    summary["parameters_created"].append(parameter.name)

            mapping_model.objects.create(
                **{connection_field: connection},
                adl_parameter=parameter,
                file_variable_name=name,
                file_variable_unit=file_unit,
                sort_order=next_order,
            )
            next_order += 1
            existing_names.add(name)
            summary["created"] += 1

    return summary
