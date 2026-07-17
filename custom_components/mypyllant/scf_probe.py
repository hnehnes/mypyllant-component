"""Einmalige Sonde: welche Base-URL bedient ein scf-System?

Hintergrund: `service-connected-control/scf/v1/systems/{id}` liefert 404. Die Annahme
`scf → scf` war aus vrc700 abgeleitet, stimmt aber offensichtlich nicht — bei `tli` ist
das Pfadsegment ja auch nicht `tli`, sondern `end-user-app-api`. Das Mapping steht nicht
im App-Bundle (die Harmonized-Base kommt dort aus einem Feature-Flag-Store), also wird
es hier empirisch bestimmt.

Läuft EINMAL beim ersten Coordinator-Refresh, macht ausschließlich GETs und schreibt
Statuscodes ins Log. Danach entfernen.
"""

from __future__ import annotations

import logging

import myPyllant.api
from myPyllant.api import MyPyllantAPI

_LOGGER = logging.getLogger(__name__)

ROOT = "https://api.vaillant-group.com/service-connected-control"

# Kandidaten, begründet:
#   1  wie tli: Suffix hinter der systemId statt eigener Base
#   2  bereits widerlegt (404) — als Kontrollpunkt drin, damit das Log selbsterklärend ist
#   3  ganz ohne Suffix
#   4  system-control, das myPyllant für Steueraufrufe ohnehin nutzt
#   5  scf-Base plus tli-artiges Suffix
#   6/7 Versionsvarianten
def _candidates(sid: str) -> list[tuple[str, str]]:
    return [
        ("1 tli-artiges Suffix", f"{ROOT}/end-user-app-api/v1/systems/{sid}/scf"),
        ("2 scf/v1 (bekannt 404)", f"{ROOT}/scf/v1/systems/{sid}"),
        ("3 ohne Suffix", f"{ROOT}/end-user-app-api/v1/systems/{sid}"),
        ("4 system-control", f"{ROOT}/system-control/v1/systems/{sid}"),
        ("5 scf/v1 + Suffix", f"{ROOT}/scf/v1/systems/{sid}/scf"),
        ("6 scf/v2", f"{ROOT}/scf/v2/systems/{sid}"),
        ("7 end-user-app-api/v2", f"{ROOT}/end-user-app-api/v2/systems/{sid}"),
    ]


async def probe(api: MyPyllantAPI) -> None:
    try:
        homes = [h async for h in api.get_homes()]
    except Exception as exc:
        _LOGGER.error("SCF-PROBE: get_homes fehlgeschlagen: %s", exc)
        return

    _LOGGER.error("SCF-PROBE: %d Home(s) gefunden", len(homes))
    for home in homes:
        sid = home.system_id
        # Rohantwort von /homes protokollieren: die Feldnamen sind für die weitere
        # Analyse wertvoll und sonst nirgends dokumentiert.
        _LOGGER.error(
            "SCF-PROBE: home nomenclature=%s cag=%s migration_state=%s fw=%s",
            getattr(home, "nomenclature", None),
            getattr(home, "cag", None),
            getattr(home, "migration_state", None),
            getattr(home, "firmware_version", None),
        )
        for label, url in _candidates(sid):
            try:
                async with api.aiohttp_session.get(
                    url, headers=api.get_authorized_headers()
                ) as r:
                    body = (await r.text())[:180]
                    _LOGGER.error("SCF-PROBE: %-24s → %s | %s", label, r.status, body)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE: %-24s → EXC %s", label, exc)


_done = False


def install() -> None:
    """get_systems() umhüllen, damit die Sonde genau einmal mit echter Session läuft."""
    global _done
    if getattr(myPyllant.api, "_scf_probe_installed", False):
        return
    myPyllant.api._scf_probe_installed = True

    original = MyPyllantAPI.get_systems

    # get_systems ist ein ASYNC GENERATOR (es yielded System-Objekte). Der Wrapper muss
    # deshalb selbst einer sein und durchreichen — ein `return await original(...)`
    # würde die Integration still zerstören.
    async def wrapper(self, *a, **kw):
        global _done
        if not _done:
            _done = True
            try:
                await probe(self)
            except Exception as exc:
                _LOGGER.error("SCF-PROBE: unerwartet: %s", exc)
        async for system in original(self, *a, **kw):
            yield system

    MyPyllantAPI.get_systems = wrapper
    _LOGGER.error("SCF-PROBE: installiert")


install()
