"""Einmalige Sonde (Runde 3): wo liegt die THERMISCHE Telemetrie eines scf-Systems?

Stand:
  200 → /homes, /homes/{serial}, /homes/{serial}/overview, /homes/{serial}/status
        /systems/{uuid}/meta-info/control-identifier  ("scf")
        /systems/{uuid}/meta-info/time-zone
        /emf/v2/{uuid}/currentSystem                  (Energiedaten!)
  404 → /systems/{uuid} unter jeder probierten Base

Muster: Unter der Legacy-Base existieren die SUB-Ressourcen, nur die Sammelressource
/systems/{uuid} nicht. Leithypothese dieser Runde: dann existieren die Zonen-/DHW-
Ressourcen evtl. DIREKT unter /systems/{uuid}/… — ohne das /tli-Segment, das myPyllant
für TLI-Regler einschiebt.

Energie liefert emf/v2 bereits; gesucht ist Vorlauf/Rücklauf/Sole/Modi/DHW.

Nur GETs. Nach Auswertung entfernen.
"""

from __future__ import annotations

import json
import logging

import myPyllant.api
from myPyllant.api import MyPyllantAPI

_LOGGER = logging.getLogger(__name__)

ROOT = "https://api.vaillant-group.com/service-connected-control"
LEGACY = f"{ROOT}/end-user-app-api/v1"


def _candidates(sid: str, serial: str) -> list[tuple[str, str]]:
    return [
        # Leithypothese: Sub-Ressourcen direkt unter /systems/{uuid}, ohne /tli
        ("10 systems/{id}/zones", f"{LEGACY}/systems/{sid}/zones"),
        ("11 systems/{id}/zone/0", f"{LEGACY}/systems/{sid}/zone/0"),
        ("12 systems/{id}/dhw/0", f"{LEGACY}/systems/{sid}/domestic-hot-water/0"),
        ("13 systems/{id}/circuits", f"{LEGACY}/systems/{sid}/circuits"),
        ("14 systems/{id}/state", f"{LEGACY}/systems/{sid}/state"),
        ("15 systems/{id}/status", f"{LEGACY}/systems/{sid}/status"),
        ("16 systems/{id}/devices", f"{LEGACY}/systems/{sid}/devices"),
        # scf-Base mit Sub-Ressource (Sammelressource dort war 404 — Sub evtl. nicht)
        ("17 scf/v1 .../zones", f"{ROOT}/scf/v1/systems/{sid}/zones"),
        ("18 scf/v1 .../zone/0", f"{ROOT}/scf/v1/systems/{sid}/zone/0"),
        # emf-Nachbarn: currentSystem liefert 200, evtl. gibt es mehr
        ("19 emf/v2 devices", f"{LEGACY}/emf/v2/{sid}/devices"),
    ]


async def _dump(api: MyPyllantAPI, label: str, url: str, limit: int = 3000) -> None:
    try:
        async with api.aiohttp_session.get(
            url, headers=api.get_authorized_headers()
        ) as r:
            body = (await r.text())[:limit]
            _LOGGER.error("SCF-PROBE3-DUMP %s (%s):\n%s", label, r.status, body)
    except Exception as exc:
        _LOGGER.error("SCF-PROBE3-DUMP %s → EXC %s", label, str(exc)[:150])


async def probe(api: MyPyllantAPI) -> None:
    try:
        async with api.aiohttp_session.get(
            f"{LEGACY}/homes", headers=api.get_authorized_headers()
        ) as r:
            homes_json = json.loads(await r.text())
    except Exception as exc:
        _LOGGER.error("SCF-PROBE3: /homes fehlgeschlagen: %s", exc)
        return

    for h in homes_json:
        sid = h.get("systemId", "")
        serial = h.get("serialNumber", "")

        for label, url in _candidates(sid, serial):
            try:
                async with api.aiohttp_session.get(
                    url, headers=api.get_authorized_headers()
                ) as r:
                    body = (await r.text())[:200]
                    mark = "★★★" if r.status == 200 else "   "
                    _LOGGER.error("SCF-PROBE3: %s %-22s → %s | %s", mark, label, r.status, body)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE3:     %-22s → EXC %s", label, str(exc)[:110])

        # Volldumps der bekannt funktionierenden Endpunkte: "overview" ist der
        # Dashboard-Endpunkt der App und der wahrscheinlichste Träger der Telemetrie.
        await _dump(api, "homes/{serial}/overview", f"{LEGACY}/homes/{serial}/overview")
        await _dump(api, "emf/v2/currentSystem", f"{LEGACY}/emf/v2/{sid}/currentSystem", 2500)


_done = False


def install() -> None:
    global _done
    if getattr(myPyllant.api, "_scf_probe_installed", False):
        return
    myPyllant.api._scf_probe_installed = True

    original = MyPyllantAPI.get_systems

    # get_systems ist ein ASYNC GENERATOR — Wrapper muss selbst einer sein.
    async def wrapper(self, *a, **kw):
        global _done
        if not _done:
            _done = True
            try:
                await probe(self)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE3: unerwartet: %s", exc)
        async for system in original(self, *a, **kw):
            yield system

    MyPyllantAPI.get_systems = wrapper
    _LOGGER.error("SCF-PROBE3: installiert")


install()
