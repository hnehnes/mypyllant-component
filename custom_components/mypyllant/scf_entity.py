"""HA-Entitäten für scf-Systeme, generisch aus den ScfPoints erzeugt.

Lese-Schicht (Sensor / Binary-Sensor). Steuerung (Number/Select/Switch mit Schreib-
Endpunkten) folgt separat.
"""

from __future__ import annotations

import re

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPressure, UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.coordinator import SystemCoordinator
from custom_components.mypyllant.scf import ScfPoint, ScfSystem


def _snake(text: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()


def _human(text: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", text).strip().title()


class ScfEntity(CoordinatorEntity):
    """Basis: hält System-ID + Punkt-ID und liest den Punkt bei jedem Zugriff frisch
    aus den Coordinator-Daten (damit Updates ankommen)."""

    coordinator: SystemCoordinator
    _attr_has_entity_name = False

    def __init__(self, coordinator: SystemCoordinator, point: ScfPoint) -> None:
        super().__init__(coordinator)
        self.system_id = point.system_id
        self.suffix = point.unique_suffix
        self._section = point.section
        self._key = point.key

    @property
    def _system(self) -> ScfSystem | None:
        for s in self.coordinator.scf_systems:
            if s.system_id == self.system_id:
                return s
        return None

    @property
    def point(self) -> ScfPoint | None:
        system = self._system
        if not system:
            return None
        for p in system.points:
            if p.unique_suffix == self.suffix:
                return p
        return None

    @property
    def available(self) -> bool:
        return self.point is not None

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_scf_{self.system_id}_{self.suffix}"

    @property
    def name(self) -> str:
        return f"{self._section} {_human(self._key)}"

    @property
    def device_info(self) -> DeviceInfo:
        system = self._system
        return DeviceInfo(
            identifiers={(DOMAIN, f"scf_{self.system_id}")},
            name=system.home_name if system else "Vaillant iQconnect",
            manufacturer="Vaillant",
            model=system.nomenclature if system else "iQconnect",
        )


# --- Einheit / Device-Class aus dem Feldnamen ableiten -----------------------
_TEMP_RE = re.compile(r"temperature|setpoint", re.IGNORECASE)
_PRESS_RE = re.compile(r"pressure", re.IGNORECASE)


def _sensor_traits(key: str) -> tuple[str | None, str | None, str | None]:
    """(unit, device_class, state_class) heuristisch aus dem Feldnamen."""
    if _PRESS_RE.search(key):
        return UnitOfPressure.BAR, SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT
    if _TEMP_RE.search(key) and "mode" not in key.lower():
        return (
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        )
    return None, None, None


class ScfSensor(ScfEntity, SensorEntity):
    def __init__(self, coordinator: SystemCoordinator, point: ScfPoint) -> None:
        super().__init__(coordinator, point)
        unit, device_class, state_class = _sensor_traits(point.key)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def native_value(self):
        point = self.point
        if not point:
            return None
        value = point.value
        if isinstance(value, float):
            return round(value, 2)
        return value


class ScfBinarySensor(ScfEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        point = self.point
        return bool(point.value) if point else None


# --- Steuerung ---------------------------------------------------------------
from homeassistant.components.number import NumberEntity  # noqa: E402
from homeassistant.components.select import SelectEntity  # noqa: E402

from custom_components.mypyllant.scf_write import patch_value  # noqa: E402


class ScfWriteMixin:
    """Gemeinsam: schreiben und danach Coordinator-Refresh anstoßen."""

    async def _write(self, value) -> None:
        point = self.point  # type: ignore[attr-defined]
        if not point:
            return
        await patch_value(
            self.coordinator.api,  # type: ignore[attr-defined]
            point.path,
            self.system_id,  # type: ignore[attr-defined]
            value,
        )
        await self.coordinator.async_request_refresh()  # type: ignore[attr-defined]


class ScfNumber(ScfEntity, ScfWriteMixin, NumberEntity):
    def __init__(self, coordinator: SystemCoordinator, point: ScfPoint) -> None:
        super().__init__(coordinator, point)
        meta = point.metadata or {}
        if meta.get("minimum") is not None:
            self._attr_native_min_value = meta["minimum"]
        if meta.get("maximum") is not None:
            self._attr_native_max_value = meta["maximum"]
        if meta.get("stepSize"):
            self._attr_native_step = meta["stepSize"]
        unit, device_class, _ = _sensor_traits(point.key)
        self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = device_class

    @property
    def native_value(self):
        point = self.point
        return point.value if point else None

    async def async_set_native_value(self, value: float) -> None:
        # LONG-Felder (z.B. Ladezeiten) ganzzahlig senden
        point = self.point
        if point and point.mtype == "LONG":
            value = int(value)
        await self._write(value)


class ScfSelect(ScfEntity, ScfWriteMixin, SelectEntity):
    def __init__(self, coordinator: SystemCoordinator, point: ScfPoint) -> None:
        super().__init__(coordinator, point)
        self._attr_options = (point.metadata or {}).get("allowedValues", [])

    @property
    def current_option(self):
        point = self.point
        return point.value if point else None

    async def async_select_option(self, option: str) -> None:
        await self._write(option)
