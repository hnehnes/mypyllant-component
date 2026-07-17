"""Einmalige No-Op-Sonde: den ECHTEN Heizkurven-Schreibpfad für scf finden.

Nutzer bestätigt: über die App IST die Heizkurve schreibbar. /scf/v1/.../circuit/1/
heating-curve liefert aber 404 (No-Op-Test). Diese Sonde probiert weitere Endpunkt-/
Body-Kandidaten — alle mit dem AKTUELLEN Wert (idempotent, ändert nichts) — und loggt die
Statuscodes. 200 = gefunden.

Nach Auswertung entfernen.
"""

from __future__ import annotations

import logging

import myPyllant.api
from myPyllant.api import MyPyllantAPI
from myPyllant.const import API_URL_BASE, SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

ROOT = "https://api.vaillant-group.com/service-connected-control"
CURRENT_HEATING_CURVE = 0.3  # aktueller Wert lt. Restore-Punkt → No-Op


def _candidates(sid: str) -> list[tuple[str, str, dict]]:
    ci = "1"  # Circuit-Index aus dem State (circuitSettings["1"])
    return [
        ("A system-control circuit(sg)", f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{sid}/circuit/{ci}/heating-curve", {"heatingCurve": CURRENT_HEATING_CURVE}),
        ("B system-control circuits(pl)", f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{sid}/circuits/{ci}/heating-curve", {"heatingCurve": CURRENT_HEATING_CURVE}),
        ("C system-control setPoint",     f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{sid}/circuit/{ci}/heating-curve", {"setPoint": CURRENT_HEATING_CURVE}),
        ("D end-user-app-api scf/circuit",f"{ROOT}/end-user-app-api/v1/systems/{sid}/scf/circuit/{ci}/heating-curve", {"heatingCurve": CURRENT_HEATING_CURVE}),
        ("E end-user-app-api circuit",    f"{ROOT}/end-user-app-api/v1/systems/{sid}/circuit/{ci}/heating-curve", {"heatingCurve": CURRENT_HEATING_CURVE}),
        ("F scf/v1 circuit index 0",      f"{API_URL_BASE['scf']}/systems/{sid}/circuit/0/heating-curve", {"heatingCurve": CURRENT_HEATING_CURVE}),
    ]


async def probe(api: MyPyllantAPI) -> None:
    homes = [h async for h in api.get_homes()]
    if not homes:
        _LOGGER.error("SCF-HC: keine Homes")
        return
    sid = homes[0].system_id
    for label, url, body in _candidates(sid):
        try:
            async with api.aiohttp_session.patch(
                url, json=body, headers=api.get_authorized_headers()
            ) as r:
                text = (await r.text())[:120]
                mark = "★★★ 200" if r.status == 200 else f"[{r.status}]"
                _LOGGER.error("SCF-HC %s %-30s %s | %s", mark, label, url.split("/systems/")[1], text)
        except Exception as exc:
            _LOGGER.error("SCF-HC EXC %-30s %s", label, str(exc)[:100])


_done = False


def install() -> None:
    if getattr(myPyllant.api, "_scf_hc_probe", False):
        return
    myPyllant.api._scf_hc_probe = True
    original = MyPyllantAPI.get_systems

    async def wrapper(self, *a, **kw):
        global _done
        if not _done:
            _done = True
            try:
                await probe(self)
            except Exception as exc:
                _LOGGER.error("SCF-HC: unerwartet: %s", exc)
        async for s in original(self, *a, **kw):
            yield s

    MyPyllantAPI.get_systems = wrapper
    _LOGGER.error("SCF-HC: installiert")


install()
