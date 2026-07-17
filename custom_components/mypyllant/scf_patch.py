"""Laufzeit-Patch: macht myPyllant den Control Identifier 'scf' bekannt.

Hintergrund
-----------
Vaillant-Geräte der iQconnect-Generation (z. B. geoCOMPACT VWS ../8.1 iQ) melden unter
    GET /systems/{systemId}/meta-info/control-identifier
den Wert ``scf``. myPyllants ``ControlIdentifier``-Enum kennt nur ``tli``/``vrc700``/
``unsupported`` und hat keinen ``_missing_``-Handler, also:

    ValueError: 'scf' is not a valid ControlIdentifier      # api.py:1314

Das passiert in ``get_systems()`` beim ersten Home und bricht den kompletten Datenabruf ab
→ „No systems available", 0 Geräte, 0 Entitäten.

Status
------
In diesem Fork ist der Patch bereits eingebunden: ``__init__.py`` importiert ihn direkt
unter ``from __future__ import annotations``. Manuell einzuspielen ist nichts.

Dieser Fork ist eine **Brücke**, bis myPyllant ``scf`` selbst kennt. Sobald der Upstream-Fix
in signalkraft/myPyllant gemerged ist, sollte man wieder auf die offizielle Integration
zurückwechseln (HACS → Custom-Repo entfernen → offizielle Version neu installieren).

Warum Monkeypatch statt Edit in site-packages
---------------------------------------------
myPyllant liegt im HA-Core-Container unter ``/usr/local/lib/python3.14/site-packages/``.
Das ist von Studio Code Server / File editor aus nicht erreichbar (die sehen nur ``/config``)
und der Edit ginge beim nächsten Core-Update verloren. ``/config`` ist der Ort, an den man
als Nutzer herankommt.
"""

from __future__ import annotations

import logging

import myPyllant.api
import myPyllant.enums
from myPyllant.const import API_URL_BASE
from myPyllant.enums import MyPyllantEnum

_LOGGER = logging.getLogger(__name__)

SCF = "scf"

# Abgeleitet aus dem URL-Template der myVAILLANT-App (3.8.0, React-Native-Bundle):
#     /{controlIdentifier}/v1/systems/{systemId}/...
# Deckt sich mit dem bekannten vrc700 → service-connected-control/vrc700/v1.
SCF_API_URL_BASE = "https://api.vaillant-group.com/service-connected-control/scf/v1"


class ControlIdentifier(MyPyllantEnum):
    """Ersetzt myPyllants Enum um den Wert ``scf``.

    Nachbau statt Erweiterung, weil Python-Enums nach Klassenerstellung keine Member mehr
    aufnehmen. Die Properties müssen mitkopiert werden — models.py ruft ``.is_vrc700`` auf
    Instanzen auf, die api.py mit dieser Klasse erzeugt.
    """

    TLI = "tli"
    VRC700 = "vrc700"
    SCF = SCF
    UNSUPPORTED = "unsupported"

    @property
    def is_vrc700(self) -> bool:
        return self is ControlIdentifier.VRC700

    @property
    def is_unsupported(self) -> bool:
        return self is ControlIdentifier.UNSUPPORTED

    @property
    def is_scf(self) -> bool:
        return self is ControlIdentifier.SCF


def apply() -> None:
    """Idempotent anwendbar — HA importiert Module gelegentlich mehrfach."""
    if getattr(myPyllant.api, "_scf_patch_applied", False):
        return

    # API_URL_BASE ist ein dict und wird per `from ... import` als OBJEKT geteilt.
    # In-place-Mutation wirkt daher in const.py und api.py gleichzeitig — kein Rebind nötig.
    API_URL_BASE.setdefault(SCF, SCF_API_URL_BASE)

    # Die Enum-KLASSE wird dagegen per `from ... import` in jeden Namespace kopiert.
    # Ein Rebind in enums.py allein bliebe für api.py wirkungslos → beide setzen.
    # models.py braucht keinen Rebind: dort ist ControlIdentifier nur Typannotation
    # (kein Konstruktor, kein match) — verifiziert in myPyllant 0.9.16.
    myPyllant.enums.ControlIdentifier = ControlIdentifier
    myPyllant.api.ControlIdentifier = ControlIdentifier

    myPyllant.api._scf_patch_applied = True
    _LOGGER.info(
        "myPyllant um Control Identifier '%s' erweitert (Base: %s)", SCF, SCF_API_URL_BASE
    )


apply()
