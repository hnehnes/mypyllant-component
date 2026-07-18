"""Button platform — currently only for scf/iQconnect systems (DHW boost).

myPyllant has no button platform. The scf domestic-hot-water one-time charge ("boost",
as in the app and the device's hot-water menu) is a command — POST .../boost to start,
DELETE .../boost to cancel — not a writable state leaf, so it cannot come out of
walk_state(). It is created here explicitly: a start button and a cancel button per DHW
circuit.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SystemCoordinator
from .utils import EntityList

_LOGGER = logging.getLogger(__name__)


def _dhw_indices(scf_system) -> list[str]:
    """Distinct domestic-hot-water indices present in the parsed state, order-preserving."""
    seen: list[str] = []
    for point in scf_system.points:
        path = point.path
        if len(path) >= 2 and path[0] == "domesticHotWaterSettings" and path[1] not in seen:
            seen.append(path[1])
    return seen


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SystemCoordinator = hass.data[DOMAIN][config.entry_id][
        "system_coordinator"
    ]
    from .scf_entity import ScfBoostButton

    buttons: EntityList[ButtonEntity] = EntityList()
    for scf_system in getattr(coordinator, "scf_systems", []):
        for index in _dhw_indices(scf_system):
            buttons.append(
                lambda s=scf_system, i=index: ScfBoostButton(
                    coordinator, s.system_id, i, True
                )
            )
            buttons.append(
                lambda s=scf_system, i=index: ScfBoostButton(
                    coordinator, s.system_id, i, False
                )
            )

    if not buttons:
        return
    async_add_entities(buttons)  # type: ignore
