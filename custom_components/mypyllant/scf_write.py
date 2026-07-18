"""Write mapping for scf controls.

Every entry is verified from the app decompilation: the app branches explicitly on
`controlIdentifier === 'scf'` and, for scf, calls the endpoints listed here (all under the
`system-control/v1` base, the same one the state comes from) with the given body field.

Kept deliberately SMALL: only fields whose endpoint+body are firmly established. Everything
else stays a read-only sensor for now (see scf.py). Extend as further endpoints are confirmed
via the no-op test.

Payload evidence (decompilation):
  setDomesticHotWaterCylinderTemperature → PATCH .../domestic-hot-water/{i}/cylinder-temperature  {"setpoint": <float>}
  setZoneOperationMode                   → PATCH .../zones/{i}/operation-mode                     {"operationMode": <enum>}
"""

from __future__ import annotations

import logging

from myPyllant.const import SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

# All confirmed scf write paths live under system-control/v1 — the same base as the state
# read. (/scf/v1 and the /{controlIdentifier}/v1 OpenAPI path both return 404; verified via
# no-op test on the device, 2026-07-17.)
_BASE_SC = "sc"

# Key: (top_section, subsection, leaf_key). subsection = path[2] for indexed sections
# (zone/circuit/dhw), path[1] for systemParameters.
# Value: (endpoint_template, body_key, base). {i} = index from path[1].
WRITE_MAP: dict[tuple[str, str, str], tuple[str, str, str]] = {
    # --- system-control/v1, base VERIFIED ---
    ("domesticHotWaterSettings", "configuration", "cylinderTemperatureSetpoint"): (
        "domestic-hot-water/{i}/cylinder-temperature", "setpoint", _BASE_SC,
    ),
    ("domesticHotWaterSettings", "configuration", "operationMode"): (
        "domestic-hot-water/{i}/operation-mode", "operationMode", _BASE_SC,
    ),
    ("zoneSettings", "general", "operationMode"): (
        "zones/{i}/operation-mode", "operationMode", _BASE_SC,
    ),
    ("zoneSettings", "heating", "manualModeTemperatureSetpoint"): (
        "zones/{i}/heating-temperature-setpoint", "setpoint", _BASE_SC,
    ),
    # Heating circuit: the path is system-control/v1/.../CIRCUITS (plural!) — found via a
    # no-op probe (2026-07-17: circuits/1/heating-curve {heatingCurve} → 202 Accepted).
    # circuit (singular, from the OpenAPI client) and /scf/v1 both return 404.
    ("circuitSettings", "configuration", "heatingCurve"): (
        "circuits/{i}/heating-curve", "heatingCurve", _BASE_SC,
    ),
    # Further heating-circuit setpoints (min/max flow, offsets) very likely also live under
    # circuits/{i}/... — but the body key is still untested, so only add them after a no-op
    # test of your own (they remain read-only sensors until then).
}


def _base_url(base: str, system_id: str) -> str:
    # Only one base for now; the parameter stays in case Vaillant later enables further
    # write paths (e.g. scf/v1).
    return f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{system_id}"


def map_key(path: list[str]) -> tuple[str, str, str] | None:
    """(top, subsection, leaf) from a point path; None if too short."""
    if len(path) < 3:
        return None
    top = path[0]
    if top == "systemParameters":
        return (top, path[1], path[-1])
    # indexed sections: path = [top, index, subsection, ..., leaf]
    if len(path) >= 4:
        return (top, path[2], path[-1])
    return None


def write_spec(path: list[str]) -> tuple[str, str, str] | None:
    """(endpoint_template, body_key, base) for a point, or None if not writable."""
    key = map_key(path)
    return WRITE_MAP.get(key) if key else None


def build_url(path: list[str], system_id: str) -> str | None:
    spec = write_spec(path)
    if not spec:
        return None
    endpoint_template, _, base = spec
    index = path[1] if len(path) >= 2 else ""
    suffix = endpoint_template.format(i=index)
    return f"{_base_url(base, system_id)}/{suffix}"


async def patch_value(api, path: list[str], system_id: str, value) -> None:
    """PATCH the value to the mapped endpoint. Raises on HTTP error."""
    spec = write_spec(path)
    url = build_url(path, system_id)
    if not spec or not url:
        raise ValueError(f"No write endpoint for {path}")
    _, body_key, _ = spec
    body = {body_key: value}
    _LOGGER.debug("scf PATCH %s %s", url, body)
    async with api.aiohttp_session.patch(
        url, json=body, headers=api.get_authorized_headers()
    ) as resp:
        resp.raise_for_status()


# --- DHW Boost (Einmalladung / one-time cylinder charge) --------------------
#
# The boost is a COMMAND, not a value-set: the app (and the device's hot-water menu)
# start it via POST and cancel it via DELETE on
#     .../domestic-hot-water/{i}/boost   (no request body)
# It is therefore not part of WRITE_MAP (which only maps writable state leaves to a
# PATCH {key: value}) and gets its own dedicated call, surfaced as a Button entity.
#
# Base: the same system-control/v1 base as every other confirmed scf write (…/scf/v1 and
# the /{controlIdentifier}/v1 OpenAPI path both 404). The POST/DELETE pair itself is not
# yet no-op-verified at the device — the base is the established scf write base, but the
# first live POST actually starts a charge, so treat the first run as the verification.
async def call_boost(api, system_id: str, dhw_index: str, start: bool) -> None:
    """Start (POST) or cancel (DELETE) the DHW one-time cylinder charge. Raises on HTTP error.

    The start POST must carry an empty JSON body ``{}`` — this sets
    ``Content-Type: application/json``, without which the Vaillant WAF rejects the
    request (HTTP 499 "The requested URL was rejected"). The cancel DELETE takes no
    body. Matches myPyllant's ``boost_domestic_hot_water`` / ``cancel_hot_water_boost``.
    """
    url = f"{_base_url(_BASE_SC, system_id)}/domestic-hot-water/{dhw_index}/boost"
    headers = api.get_authorized_headers()
    if start:
        _LOGGER.debug("scf POST %s", url)
        ctx = api.aiohttp_session.post(url, json={}, headers=headers)
    else:
        _LOGGER.debug("scf DELETE %s", url)
        ctx = api.aiohttp_session.delete(url, headers=headers)
    async with ctx as resp:
        resp.raise_for_status()


# --- Wochenpläne (TIME_PERIODS) ---------------------------------------------
#
# Bewusst GETRENNT von WRITE_MAP gehalten: Schedules bleiben Lese-Sensoren
# (scf.py: platform()/has_write bleiben unberührt) und werden ausschließlich über den
# Service mypyllant.scf_set_schedule geschrieben. Der Body ist NICHT {key: value}, sondern
# das komplette Tages-Dict {monday: [{startTime,endTime,setpoint}], …} (Tages-Keys klein,
# Minuten seit Mitternacht). Alle Endpunkte unter derselben system-control/v1-Base.
#
# Key: (top_section, subsection, leaf_key) — identisch zu map_key() für indexierte Sektionen.
# Value: (endpoint_template, setpoint_required). {i} = Index aus path[1].
SCHEDULE_MAP: dict[tuple[str, str, str], tuple[str, bool]] = {
    # WW-Zirkulationspumpe — VERIFIZIERT (202 Accepted, 2026-07-17). Kein setpoint (Pumpe).
    ("domesticHotWaterSettings", "configuration", "circulationPumpTimePeriods"): (
        "domestic-hot-water/{i}/circulation-pump-time-periods", False,
    ),
    # Zonen-Heizzeiten — Endpunkt aus der system-control-Liste; setpointRequiredPerSlot=true
    # (5–30 °C, step 0.5). Body per No-Op zu bestätigen.
    ("zoneSettings", "heating", "timePeriods"): (
        "zones/{i}/heating-time-periods", True,
    ),
    # WW-Ladezeiten (circuitTimePeriods) — VERIFIZIERT per No-Op-Probe (202 Accepted,
    # 2026-07-17). Die anderen Kandidaten (time-periods, loading-time-periods,
    # dhw-time-periods) lieferten 404. Kein setpoint (WW-Ladung).
    ("domesticHotWaterSettings", "configuration", "circuitTimePeriods"): (
        "domestic-hot-water/{i}/circuit-time-periods", False,
    ),
}

_DAY_ALIASES: dict[str, str] = {
    "monday": "monday", "mon": "monday", "mo": "monday",
    "tuesday": "tuesday", "tue": "tuesday", "di": "tuesday", "tu": "tuesday",
    "wednesday": "wednesday", "wed": "wednesday", "mi": "wednesday", "we": "wednesday",
    "thursday": "thursday", "thu": "thursday", "do": "thursday", "th": "thursday",
    "friday": "friday", "fri": "friday", "fr": "friday",
    "saturday": "saturday", "sat": "saturday", "sa": "saturday",
    "sunday": "sunday", "sun": "sunday", "so": "sunday", "su": "sunday",
}


def schedule_spec(path: list[str]) -> tuple[str | None, bool] | None:
    """(endpoint_template, setpoint_required) für einen Schedule-Punkt, oder None."""
    key = map_key(path)
    return SCHEDULE_MAP.get(key) if key else None


def build_schedule_url(path: list[str], system_id: str) -> str | None:
    spec = schedule_spec(path)
    if not spec or not spec[0]:
        return None
    endpoint_template, _ = spec
    index = path[1] if len(path) >= 2 else ""
    return f"{_base_url(_BASE_SC, system_id)}/{endpoint_template.format(i=index)}"


def _to_minutes(value) -> int:
    """'HH:MM' oder Minuten (int/float/str) → Minuten seit Mitternacht (0–1440)."""
    if isinstance(value, bool):  # bool ist int-Subklasse — hier nie gemeint
        raise ValueError(f"Ungültige Zeit: {value!r}")
    if isinstance(value, (int, float)):
        minutes = int(value)
    elif isinstance(value, str) and ":" in value:
        hh, _, mm = value.partition(":")
        minutes = int(hh) * 60 + int(mm)
    elif isinstance(value, str):
        minutes = int(value)
    else:
        raise ValueError(f"Ungültige Zeit: {value!r}")
    if not 0 <= minutes <= 1440:
        raise ValueError(f"Zeit außerhalb 0–1440 min: {value!r}")
    return minutes


def _slot_value(slot: dict, *keys):
    for k in keys:
        if k in slot and slot[k] is not None:
            return slot[k]
    return None


def build_schedule_body(schedule: dict, setpoint_required: bool) -> dict:
    """Nutzer-Schedule → API-Body {monday: [{startTime,endTime,setpoint}], …}.

    Tages-Keys tolerant (englisch/deutsch, lang/kurz) → lowercase-englisch. Zeiten als
    'HH:MM' oder Minuten. setpoint: bei setpoint_required Pflicht je Slot (5–30 geklemmt),
    sonst immer null (Pumpe/WW-Ladung)."""
    if not isinstance(schedule, dict):
        raise ValueError("schedule muss ein Dict {tag: [slots]} sein")
    body: dict[str, list[dict]] = {}
    for raw_day, slots in schedule.items():
        day = _DAY_ALIASES.get(str(raw_day).strip().lower())
        if not day:
            raise ValueError(f"Unbekannter Wochentag: {raw_day!r}")
        out_slots: list[dict] = []
        for slot in slots or []:
            start = _slot_value(slot, "startTime", "start_time", "start")
            end = _slot_value(slot, "endTime", "end_time", "end")
            if start is None or end is None:
                raise ValueError(f"Slot braucht Start- und Endzeit: {slot!r} ({day})")
            entry = {"startTime": _to_minutes(start), "endTime": _to_minutes(end)}
            if setpoint_required:
                sp = _slot_value(slot, "setpoint", "temperature")
                if sp is None:
                    raise ValueError(
                        f"Zonen-Heizzeiten brauchen setpoint je Slot: {slot!r} ({day})"
                    )
                entry["setpoint"] = max(5.0, min(30.0, float(sp)))
            else:
                entry["setpoint"] = None
            out_slots.append(entry)
        body[day] = out_slots
    return body


async def patch_schedule(api, path: list[str], system_id: str, schedule: dict) -> None:
    """PATCH einen kompletten Wochenplan an den passenden Endpunkt. Raises on HTTP error."""
    spec = schedule_spec(path)
    if not spec:
        raise ValueError(f"Kein Schedule-Endpunkt für {path}")
    endpoint_template, setpoint_required = spec
    url = build_schedule_url(path, system_id)
    if not endpoint_template or not url:
        raise ValueError(
            f"Schedule-Endpunkt für {map_key(path)} noch nicht bestätigt — nicht schreibbar"
        )
    body = build_schedule_body(schedule, setpoint_required)
    _LOGGER.debug("scf PATCH schedule %s %s", url, body)
    async with api.aiohttp_session.patch(
        url, json=body, headers=api.get_authorized_headers()
    ) as resp:
        resp.raise_for_status()
