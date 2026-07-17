"""Select platform — currently only for scf/iQconnect systems (operation modes).

myPyllant models modes via climate/water_heater; scf enum fields with `allowedValues`
become generic Select entities here.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SystemCoordinator
from .utils import EntityList

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SystemCoordinator = hass.data[DOMAIN][config.entry_id][
        "system_coordinator"
    ]
    from .scf_entity import ScfSelect

    selects: EntityList[SelectEntity] = EntityList()
    for scf_system in getattr(coordinator, "scf_systems", []):
        for point in scf_system.by_platform("select"):
            selects.append(lambda p=point: ScfSelect(coordinator, p))

    if not selects:
        return
    async_add_entities(selects)  # type: ignore
