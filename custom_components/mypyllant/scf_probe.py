"""Einmalige Sonde (Runde 2): wo liegen die Systemdaten eines scf/iQconnect-Systems?

Stand nach Runde 1 — alle 7 Kandidaten unter /systems/{uuid} lieferten 404, ABER:
  * /homes                                        → 200, 1 Home, nomenclature=iQconnect
  * /systems/{uuid}/meta-info/control-identifier  → 200, "scf"
Das System existiert also unter der Legacy-Base; nur die Sammelressource /systems/{uuid}
gibt es für scf nicht.

Runde 2 prüft die home-basierte Spur: das App-Bundle adressiert Homes mit der
SERIENNUMMER (/homes/{serial}/overview, /homes/{serial}/status), nicht mit der UUID.

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
        # Kontrollpunkt: MUSS 200 liefern, sonst ist die Sonde selbst kaputt
        ("00 KONTROLLE ctrl-ident", f"{LEGACY}/systems/{sid}/meta-info/control-identifier"),
        # Home-basiert (Seriennummer) — die eigentliche Hypothese dieser Runde
        ("01 homes/{serial}", f"{LEGACY}/homes/{serial}"),
        ("02 homes/{serial}/overview", f"{LEGACY}/homes/{serial}/overview"),
        ("03 homes/{serial}/status", f"{LEGACY}/homes/{serial}/status"),
        # Home-basiert, aber mit UUID statt Serial
        ("04 homes/{uuid}/overview", f"{LEGACY}/homes/{sid}/overview"),
        # System-Subressourcen: kartieren, was unter /systems/{uuid} überhaupt existiert
        ("05 meta-info/time-zone", f"{LEGACY}/systems/{sid}/meta-info/time-zone"),
        ("06 emf/v2 currentSystem", f"{LEGACY}/emf/v2/{sid}/currentSystem"),
        # Serial als System-ID
        ("07 systems/{serial}", f"{LEGACY}/systems/{serial}"),
        # "Harmonized API" — Feature-Flag-Name aus dem Bundle, Base dort nicht hartcodiert
        ("08 harmonized/v1", f"{ROOT}/harmonized/v1/systems/{sid}"),
        ("09 scf/v1 homes/serial", f"{ROOT}/scf/v1/homes/{serial}"),
    ]


async def probe(api: MyPyllantAPI) -> None:
    # Roh-JSON von /homes: die Feldnamen sind nirgends dokumentiert und könnten
    # direkt verraten, wo die Systemdaten liegen.
    try:
        async with api.aiohttp_session.get(
            f"{LEGACY}/homes", headers=api.get_authorized_headers()
        ) as r:
            raw = await r.text()
        _LOGGER.error("SCF-PROBE2: /homes roh (%s): %s", r.status, raw[:1500])
        homes_json = json.loads(raw)
    except Exception as exc:
        _LOGGER.error("SCF-PROBE2: /homes fehlgeschlagen: %s", exc)
        return

    for h in homes_json:
        sid = h.get("systemId") or h.get("system_id") or ""
        serial = h.get("serialNumber") or h.get("serial_number") or ""
        _LOGGER.error("SCF-PROBE2: systemId=%s serial=%s", sid, serial)
        for label, url in _candidates(sid, serial):
            try:
                async with api.aiohttp_session.get(
                    url, headers=api.get_authorized_headers()
                ) as r:
                    body = (await r.text())[:220]
                    mark = "★★★" if r.status == 200 else "   "
                    _LOGGER.error("SCF-PROBE2: %s %-26s → %s | %s", mark, label, r.status, body)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE2:     %-26s → EXC %s", label, str(exc)[:120])


_done = False


def install() -> None:
    global _done
    if getattr(myPyllant.api, "_scf_probe_installed", False):
        return
    myPyllant.api._scf_probe_installed = True

    original = MyPyllantAPI.get_systems

    # get_systems ist ein ASYNC GENERATOR — der Wrapper muss selbst einer sein.
    async def wrapper(self, *a, **kw):
        global _done
        if not _done:
            _done = True
            try:
                await probe(self)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE2: unerwartet: %s", exc)
        async for system in original(self, *a, **kw):
            yield system

    MyPyllantAPI.get_systems = wrapper
    _LOGGER.error("SCF-PROBE2: installiert")


install()
