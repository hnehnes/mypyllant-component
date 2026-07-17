"""Schreibzuordnung für scf-Steuerung.

Jeder Eintrag ist aus dem App-Dekompilat verifiziert: Die App verzweigt im Code explizit
nach `controlIdentifier === 'scf'` und schickt für scf die hier hinterlegten Endpunkte
(alle unter der `system-control/v1`-Base, derselben, aus der auch der State kommt) mit dem
angegebenen Body-Feld.

Bewusst KLEIN gehalten: nur Felder, deren Endpunkt+Body sicher belegt sind. Alles andere
bleibt vorerst Lese-Sensor (siehe scf.py). Ausweiten, sobald weitere Endpunkte am No-Op-Test
bestätigt sind.

Payload-Belege (Dekompilat):
  setDomesticHotWaterCylinderTemperature → PATCH .../domestic-hot-water/{i}/cylinder-temperature  {"setpoint": <float>}
  setZoneOperationMode                   → PATCH .../zones/{i}/operation-mode                     {"operationMode": <enum>}
"""

from __future__ import annotations

import logging

from myPyllant.const import SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

# Alle bestätigten scf-Schreibpfade liegen unter system-control/v1 — derselben Base wie
# der State-Read. (/scf/v1 und der /{controlIdentifier}/v1-OpenAPI-Pfad liefern 404;
# per No-Op-Test am Gerät verifiziert, 2026-07-17.)
_BASE_SC = "sc"

# Schlüssel: (top_section, subsection, leaf_key). subsection = path[2] bei indexierten
# Sektionen (zone/circuit/dhw), path[1] bei systemParameters.
# Wert: (endpoint_template, body_key, base). {i} = Index aus path[1].
WRITE_MAP: dict[tuple[str, str, str], tuple[str, str, str]] = {
    # --- system-control/v1, Base VERIFIZIERT ---
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
    # Heizkreis: Pfad ist system-control/v1/.../CIRCUITS (Plural!) — per No-Op-Sonde
    # gefunden (2026-07-17: circuits/1/heating-curve {heatingCurve} → 202 Accepted).
    # circuit (Singular, aus dem OpenAPI-Client) und /scf/v1 liefern beide 404.
    ("circuitSettings", "configuration", "heatingCurve"): (
        "circuits/{i}/heating-curve", "heatingCurve", _BASE_SC,
    ),
    # Weitere Heizkreis-Setpoints (min/max-Vorlauf, Offsets) liegen sehr wahrscheinlich
    # ebenfalls unter circuits/{i}/... — Body-Schlüssel aber noch ungetestet, daher erst
    # nach eigenem No-Op-Test aufnehmen (bleiben bis dahin Lese-Sensoren).
}


def _base_url(base: str, system_id: str) -> str:
    # Aktuell nur eine Base; der Parameter bleibt, falls Vaillant weitere Schreibpfade
    # (z.B. scf/v1) später aktiviert.
    return f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{system_id}"


def map_key(path: list[str]) -> tuple[str, str, str] | None:
    """(top, subsection, leaf) aus einem Punkt-Pfad; None wenn zu kurz."""
    if len(path) < 3:
        return None
    top = path[0]
    if top == "systemParameters":
        return (top, path[1], path[-1])
    # indexierte Sektionen: path = [top, index, subsection, ..., leaf]
    if len(path) >= 4:
        return (top, path[2], path[-1])
    return None


def write_spec(path: list[str]) -> tuple[str, str, str] | None:
    """(endpoint_template, body_key, base) für einen Punkt, oder None wenn nicht schreibbar."""
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
    """PATCH den Wert an den zugeordneten Endpunkt. Wirft bei HTTP-Fehler."""
    spec = write_spec(path)
    url = build_url(path, system_id)
    if not spec or not url:
        raise ValueError(f"Kein Schreib-Endpunkt für {path}")
    _, body_key, _ = spec
    body = {body_key: value}
    _LOGGER.debug("scf PATCH %s %s", url, body)
    async with api.aiohttp_session.patch(
        url, json=body, headers=api.get_authorized_headers()
    ) as resp:
        resp.raise_for_status()
