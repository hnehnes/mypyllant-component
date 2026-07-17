"""Runtime patch: teaches myPyllant the control identifier 'scf'.

Background
----------
iQconnect-generation Vaillant devices (e.g. geoCOMPACT VWS ../8.1 iQ) report
    GET /systems/{systemId}/meta-info/control-identifier
as ``scf``. myPyllant's ``ControlIdentifier`` enum only knows ``tli``/``vrc700``/
``unsupported`` and has no ``_missing_`` handler, so:

    ValueError: 'scf' is not a valid ControlIdentifier      # api.py:1314

This happens in ``get_systems()`` on the first home and aborts the whole data fetch
→ "No systems available", 0 devices, 0 entities.

Scope
-----
This adds the ``scf`` control identifier at runtime until the myPyllant library ships it
itself. ``__init__.py`` imports this module directly under
``from __future__ import annotations``, so ``apply()`` runs before the coordinator fetches
data. Once myPyllant supports ``scf`` natively, this module can be dropped.

Why a monkeypatch instead of editing site-packages
--------------------------------------------------
The myPyllant library lives inside the HA core container under
``/usr/local/lib/pythonX.Y/site-packages/``, which is not reachable from the user-facing
``/config`` mount and where any edit would be lost on the next core update. Patching from
within the component keeps the change in a location the user controls.
"""

from __future__ import annotations

import logging

import myPyllant.api
import myPyllant.enums
from myPyllant.const import API_URL_BASE
from myPyllant.enums import MyPyllantEnum

_LOGGER = logging.getLogger(__name__)

SCF = "scf"

# Derived from the URL template of the myVAILLANT app (3.8.0, React Native bundle):
#     /{controlIdentifier}/v1/systems/{systemId}/...
# Matches the known vrc700 → service-connected-control/vrc700/v1.
SCF_API_URL_BASE = "https://api.vaillant-group.com/service-connected-control/scf/v1"


class ControlIdentifier(MyPyllantEnum):
    """Re-defines myPyllant's enum with the added value ``scf``.

    Re-defined rather than extended because Python enums cannot gain members after class
    creation. The properties must be copied along — models.py calls ``.is_vrc700`` on
    instances that api.py builds with this class.
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
    """Idempotent — HA occasionally imports modules more than once."""
    if getattr(myPyllant.api, "_scf_patch_applied", False):
        return

    # API_URL_BASE is a dict and is shared as an OBJECT via `from ... import`. Mutating it
    # in place therefore takes effect in both const.py and api.py — no rebind needed.
    API_URL_BASE.setdefault(SCF, SCF_API_URL_BASE)

    # The enum CLASS, by contrast, is copied into every namespace by `from ... import`.
    # A rebind in enums.py alone would have no effect in api.py → set both.
    # models.py needs no rebind: there ControlIdentifier is only a type annotation
    # (no constructor, no match) — verified in myPyllant 0.9.16.
    myPyllant.enums.ControlIdentifier = ControlIdentifier
    myPyllant.api.ControlIdentifier = ControlIdentifier

    myPyllant.api._scf_patch_applied = True
    _LOGGER.info(
        "Extended myPyllant with control identifier '%s' (base: %s)", SCF, SCF_API_URL_BASE
    )


apply()
