"""scf-Zweig (iQconnect / VR_NEEXT-Geräte).

Warum eigenständig: myPyllant baut `System`-Objekte aus dem Aggregat-Endpunkt
`/{controlIdentifier}/v1/systems/{id}` — den es für `scf` NICHT gibt (404). scf-Geräte
liefern ihren Zustand stattdessen unter
    GET system-control/v1/systems/{id}/state
in einem völlig anderen, aber **selbstbeschreibenden** Format:
    { "value": <x>, "metadata": {writable, type, minimum, maximum, stepSize, allowedValues}, ... }

Diese Selbstbeschreibung ist der Trick: statt 40 Entitäten von Hand zu pflegen, läuft
`walk_state()` den Baum ab und leitet Typ + Grenzen jeder Entität direkt aus `metadata` ab.
Neue Felder eines späteren Rollouts erscheinen dadurch automatisch.

Belegt in docs/scf-state-response.md (am Gerät gemessen).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from myPyllant.const import SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

SCF = "scf"


async def fetch_scf_state(api, system_id: str) -> dict:
    """Roh-State eines scf-Systems holen."""
    url = f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{system_id}/state"
    async with api.aiohttp_session.get(
        url, headers=api.get_authorized_headers()
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


# Sektionen des State-Baums → (Anzeige-Präfix, ist-indexiert)
# systemParameters ist ein einzelnes Objekt; zone/circuit/dhw sind nach Index gruppiert.
_SECTIONS = {
    "systemParameters": ("System", False),
    "zoneSettings": ("Zone", True),
    "circuitSettings": ("Kreis", True),
    "domesticHotWaterSettings": ("Warmwasser", True),
}


@dataclass
class ScfPoint:
    """Ein Blatt aus dem State-Baum, HA-tauglich klassifiziert."""

    system_id: str
    section: str            # z.B. "System", "Zone 1", "Warmwasser 1"
    key: str                # Blattname, z.B. "systemFlowTemperature"
    unique_suffix: str      # stabile ID über den Pfad
    value: Any
    metadata: dict | None
    path: list[str]         # voller Pfad für spätere Schreibzuordnung

    @property
    def writable(self) -> bool:
        return bool(self.metadata and self.metadata.get("writable") and self.metadata.get("enabled"))

    @property
    def mtype(self) -> str | None:
        return self.metadata.get("type") if self.metadata else None

    def platform(self) -> str:
        """HA-Plattform aus Wert + metadata ableiten."""
        if not self.writable:
            if isinstance(self.value, bool):
                return "binary_sensor"
            return "sensor"
        t = self.mtype
        if t == "BOOL":
            return "switch"
        if t == "ENUM":
            return "select"
        if t in ("FLOAT", "LONG"):
            return "number"
        # writable, aber komplexer Typ (TIME_PERIODS, DEFAULT-Objekte) → vorerst nur anzeigen
        if isinstance(self.value, bool):
            return "binary_sensor"
        return "sensor"


def _walk(node: Any, path: list[str], sid: str, out: list[ScfPoint]) -> None:
    """Rekursiv: ein Blatt ist ein dict mit den Schlüsseln value/metadata/lastUpdated."""
    if isinstance(node, dict) and "value" in node and "metadata" in node:
        section = _section_label(path)
        key = path[-1]
        _add_point(sid, section, key, path, node, out)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, path + [k], sid, out)


def _section_label(path: list[str]) -> str:
    top = path[0] if path else ""
    label, indexed = _SECTIONS.get(top, (top, False))
    if indexed and len(path) >= 2:
        return f"{label} {path[1]}"
    return label


def _add_point(sid, section, key, path, node, out) -> None:
    value = node.get("value")
    if value is None:
        return  # Gerät liefert dieses Feld nicht → überspringen, nicht crashen
    # Komplexe Werte (Objekte/Listen) sind keine simplen Entitäten → auslassen
    if isinstance(value, (dict, list)):
        return
    out.append(
        ScfPoint(
            system_id=sid,
            section=section,
            key=key,
            unique_suffix="_".join(path),
            value=value,
            metadata=node.get("metadata"),
            path=path,
        )
    )


def walk_state(system_id: str, state: dict) -> list[ScfPoint]:
    """Kompletten State-Baum in eine flache Liste HA-tauglicher Punkte übersetzen."""
    data = (state or {}).get("data", {})
    out: list[ScfPoint] = []
    for top in _SECTIONS:
        if top in data:
            _walk(data[top], [top], system_id, out)
    _LOGGER.debug("scf %s: %d Punkte aus State abgeleitet", system_id, len(out))
    return out


@dataclass
class ScfSystem:
    """Geparster Zustand eines scf-Systems, wie ihn die Entitäten konsumieren."""

    system_id: str
    home_name: str
    nomenclature: str
    points: list[ScfPoint] = field(default_factory=list)

    def by_platform(self, platform: str) -> list[ScfPoint]:
        return [p for p in self.points if p.platform() == platform]
