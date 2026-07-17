"""Einmal-Aktion: Zirkulationspumpen-Zeitfenster für scf setzen.

Vorgehen (sicher):
  1. Endpunkt per No-Op finden: den AKTUELLEN Plan an mehrere Kandidaten-Endpunkte
     PATCHen. Der 2xx-Kandidat ist der richtige (kein echter Change, da identisch).
  2. An genau diesen Endpunkt den NEUEN Plan schreiben.

Restore-Punkt (docs/RESTORE-POINT.md) sichert den Ausgangszustand.
Nach Erfolg entfernen.
"""

from __future__ import annotations

import logging

import myPyllant.api
from myPyllant.api import MyPyllantAPI
from myPyllant.const import SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

DHW_INDEX = "1"

# Slot-Format wie im State-Read: {startTime, endTime, setpoint}. Minuten seit Mitternacht.
def _slot(start: int, end: int) -> dict:
    return {"startTime": start, "endTime": end, "setpoint": None}


# AKTUELL (aus dem State-Dump) — für den No-Op-Endpunkttest:
CURRENT = {
    "monday": [_slot(360, 1320)],
    "tuesday": [_slot(360, 1320)],
    "wednesday": [_slot(360, 1320)],
    "thursday": [_slot(360, 1320)],
    "friday": [_slot(360, 1320)],
    "saturday": [_slot(450, 1410)],
    "sunday": [_slot(450, 1320)],
}

# NEU (Nutzer-Wunsch): Mo–Fr 05:30–06:00 + 17:30–18:00; Sa/So 08:00–08:30 + 17:30–18:00
_WEEKDAY = [_slot(330, 360), _slot(1050, 1080)]
_WEEKEND = [_slot(480, 510), _slot(1050, 1080)]
NEW = {
    "monday": _WEEKDAY, "tuesday": _WEEKDAY, "wednesday": _WEEKDAY,
    "thursday": _WEEKDAY, "friday": _WEEKDAY,
    "saturday": _WEEKEND, "sunday": _WEEKEND,
}


def _candidates(sid: str) -> list[tuple[str, str]]:
    base = f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{sid}/domestic-hot-water/{DHW_INDEX}"
    return [
        ("A hyphen", f"{base}/circulation-pump-time-windows"),
        ("B slash", f"{base}/circulation-pump/time-windows"),
    ]


async def _patch(api, url, body) -> tuple[int, str]:
    async with api.aiohttp_session.patch(
        url, json=body, headers=api.get_authorized_headers()
    ) as r:
        return r.status, (await r.text())[:150]


async def probe(api: MyPyllantAPI) -> None:
    homes = [h async for h in api.get_homes()]
    if not homes:
        _LOGGER.error("SCF-CIRC: keine Homes")
        return
    sid = homes[0].system_id

    working = None
    for label, url in _candidates(sid):
        try:
            status, text = await _patch(api, url, CURRENT)
            _LOGGER.error("SCF-CIRC no-op %-10s %s → %s | %s", label, url.split("/domestic")[0][-20:], status, text)
            if 200 <= status < 300:
                working = url
                break
        except Exception as exc:
            _LOGGER.error("SCF-CIRC no-op %-10s EXC %s", label, str(exc)[:100])

    if not working:
        _LOGGER.error("SCF-CIRC: KEIN Endpunkt akzeptiert den No-Op → neuen Plan NICHT geschrieben")
        return

    try:
        status, text = await _patch(api, working, NEW)
        mark = "★★★ NEUER PLAN GESETZT" if 200 <= status < 300 else "FEHLER"
        _LOGGER.error("SCF-CIRC %s → %s | %s", mark, status, text)
    except Exception as exc:
        _LOGGER.error("SCF-CIRC neuer Plan EXC %s", str(exc)[:120])


_done = False


def install() -> None:
    if getattr(myPyllant.api, "_scf_circ_probe", False):
        return
    myPyllant.api._scf_circ_probe = True
    import custom_components.mypyllant.scf as scf_mod

    original = scf_mod.fetch_scf_state

    async def wrapper(api, system_id):
        state = await original(api, system_id)
        global _done
        if not _done:
            _done = True
            try:
                await probe(api)
            except Exception as exc:
                _LOGGER.error("SCF-CIRC: unerwartet: %s", exc)
        return state

    scf_mod.fetch_scf_state = wrapper
    _LOGGER.error("SCF-CIRC: installiert")


install()
