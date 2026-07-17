"""EINMALIGE, minimale Sonde: liefert /systems/{id}/state Telemetrie für scf?

Getestet werden nur ZWEI GETs (Quota-schonend, nach dem 403-Vorfall bewusst minimal):
  1. system-control/v1/systems/{id}/state   ← getSystemControlState aus dem App-Dekompilat
  2. end-user-app-api/v1/systems/{id}/state  ← der prefix-lose GET aus der 7-GET-Liste

Hintergrund: Der Aggregat-GET /scf/v1/systems/{id} liefert 404. `getSystemControlState`
ist der einzige noch ungetestete GET-Kandidat für thermische Live-Daten.

Läuft genau einmal beim ersten Coordinator-Refresh, nur GETs, Body wird redigiert geloggt.
Nach Auswertung entfernen.
"""

from __future__ import annotations

import json
import logging
import re

import myPyllant.api
from myPyllant.api import MyPyllantAPI

_LOGGER = logging.getLogger(__name__)

ROOT = "https://api.vaillant-group.com/service-connected-control"

# Seriennummern/Koordinaten aus dem Log halten
_REDACT = re.compile(r'"(serialNumber|latitude|longitude|street|city|postalCode)"\s*:\s*"[^"]*"',
                     re.IGNORECASE)


def _redact(text: str) -> str:
    return _REDACT.sub(r'"\1":"<RED>"', text)


async def probe(api: MyPyllantAPI) -> None:
    try:
        homes = [h async for h in api.get_homes()]
    except Exception as exc:
        _LOGGER.error("SCF-STATE: get_homes fehlgeschlagen: %s", exc)
        return
    if not homes:
        _LOGGER.error("SCF-STATE: keine Homes")
        return

    sid = homes[0].system_id
    candidates = [
        ("system-control/v1 .../state", f"{ROOT}/system-control/v1/systems/{sid}/state"),
        ("end-user-app-api/v1 .../state", f"{ROOT}/end-user-app-api/v1/systems/{sid}/state"),
    ]
    for label, url in candidates:
        try:
            async with api.aiohttp_session.get(
                url, headers=api.get_authorized_headers()
            ) as r:
                body = _redact((await r.text())[:2500])
                mark = "★★★ TREFFER" if r.status == 200 else f"[{r.status}]"
                _LOGGER.error("SCF-STATE %s %s\n%s", mark, label, body)
        except Exception as exc:
            _LOGGER.error("SCF-STATE %s → EXC %s", label, str(exc)[:120])


_done = False


def install() -> None:
    if getattr(myPyllant.api, "_scf_state_probe", False):
        return
    myPyllant.api._scf_state_probe = True
    original = MyPyllantAPI.get_systems

    # get_systems ist ein async generator — Wrapper muss selbst einer sein und durchreichen.
    async def wrapper(self, *a, **kw):
        global _done
        if not _done:
            _done = True
            try:
                await probe(self)
            except Exception as exc:
                _LOGGER.error("SCF-STATE: unerwartet: %s", exc)
        async for s in original(self, *a, **kw):
            yield s

    MyPyllantAPI.get_systems = wrapper
    _LOGGER.error("SCF-STATE: installiert")


install()
