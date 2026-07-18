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
from homeassistant.exceptions import HomeAssistantError
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


_WEEKDAYS = [
    ("monday", "Mo"), ("tuesday", "Di"), ("wednesday", "Mi"), ("thursday", "Do"),
    ("friday", "Fr"), ("saturday", "Sa"), ("sunday", "So"),
]


def _hhmm(minutes) -> str:
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "?"
    return f"{m // 60:02d}:{m % 60:02d}"


def _day_slots(day_value) -> list[str]:
    """Slot-Liste eines Tages → ['05:30–06:00', …]."""
    out = []
    for slot in day_value or []:
        out.append(f"{_hhmm(slot.get('startTime'))}–{_hhmm(slot.get('endTime'))}")
    return out


def _schedule_by_day(value: dict) -> dict[str, list[str]]:
    """{MONDAY|monday: [...]} → {'Mo': ['05:30–06:00', …], …} (Groß/Klein-tolerant)."""
    lower = {str(k).lower(): v for k, v in (value or {}).items()}
    return {short: _day_slots(lower.get(name, [])) for name, short in _WEEKDAYS}


def _schedule_summary(by_day: dict[str, list[str]]) -> str:
    """Kompakte Zusammenfassung; gleiche Tage werden zu Gruppen zusammengefasst."""
    # Tage mit identischem Plan gruppieren, Reihenfolge erhalten
    groups: list[tuple[list[str], list[str]]] = []
    for short, slots in by_day.items():
        if groups and groups[-1][1] == slots:
            groups[-1][0].append(short)
        else:
            groups.append(([short], slots))
    parts = []
    for days, slots in groups:
        rng = days[0] if len(days) == 1 else f"{days[0]}–{days[-1]}"
        parts.append(f"{rng}: {', '.join(slots) if slots else 'aus'}")
    return " · ".join(parts)[:255]


class ScfSensor(ScfEntity, SensorEntity):
    def __init__(self, coordinator: SystemCoordinator, point: ScfPoint) -> None:
        super().__init__(coordinator, point)
        if not point.is_schedule:
            unit, device_class, state_class = _sensor_traits(point.key)
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = device_class
            self._attr_state_class = state_class

    @property
    def native_value(self):
        point = self.point
        if not point:
            return None
        if point.is_schedule:
            return _schedule_summary(_schedule_by_day(point.value))
        value = point.value
        if isinstance(value, float):
            return round(value, 2)
        return value

    @property
    def extra_state_attributes(self):
        point = self.point
        if point and point.is_schedule:
            # Voller Plan pro Tag als Attribute (für Automationen/Detailansicht) plus die
            # rohen Slots (mit setpoint) unter "raw", damit ein Wochenplan verlustfrei
            # gelesen und per scf_set_schedule zurückgeschrieben werden kann.
            attrs = {day: slots for day, slots in _schedule_by_day(point.value).items()}
            attrs["raw"] = point.value
            return attrs
        return None

    async def set_schedule(self, schedule: dict) -> None:
        """Service mypyllant.scf_set_schedule — kompletten Wochenplan schreiben."""
        point = self.point
        if not point or not point.is_schedule:
            raise HomeAssistantError(
                "scf_set_schedule: Ziel ist kein Wochenplan-Sensor"
            )
        await patch_schedule(
            self.coordinator.api, point.path, self.system_id, schedule
        )
        await self.coordinator.async_request_refresh()


class ScfBinarySensor(ScfEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        point = self.point
        return bool(point.value) if point else None


# --- Steuerung ---------------------------------------------------------------
from homeassistant.components.button import ButtonEntity  # noqa: E402
from homeassistant.components.number import NumberEntity  # noqa: E402
from homeassistant.components.select import SelectEntity  # noqa: E402

from custom_components.mypyllant.scf_write import (  # noqa: E402
    call_boost,
    patch_schedule,
    patch_value,
)


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


class ScfBoostButton(CoordinatorEntity, ButtonEntity):
    """DHW one-time cylinder charge (boost) as a Button.

    Not derived from a ScfPoint: the boost is a command (POST/DELETE .../boost), not a
    writable state leaf. One system may have several DHW circuits → keyed by index.
    ``start=True`` presses trigger a charge (POST), ``start=False`` cancel it (DELETE).
    """

    coordinator: SystemCoordinator
    _attr_has_entity_name = False

    def __init__(
        self, coordinator: SystemCoordinator, system_id: str, dhw_index: str, start: bool
    ) -> None:
        super().__init__(coordinator)
        self.system_id = system_id
        self._index = dhw_index
        self._start = start
        self._attr_icon = "mdi:water-plus" if start else "mdi:water-off"

    @property
    def _system(self):
        for s in self.coordinator.scf_systems:
            if s.system_id == self.system_id:
                return s
        return None

    def _state_value(self, key: str):
        """Current value of a domesticHotWaterSettings/{i}/state/<key> point, or None."""
        system = self._system
        if not system:
            return None
        suffix = f"domesticHotWaterSettings_{self._index}_state_{key}"
        for p in system.points:
            if p.unique_suffix == suffix:
                return p.value
        return None

    @property
    def available(self) -> bool:
        # Always available while the system is present; the device rejects an invalid
        # boost itself (start needs isBoostPossible, cancel needs an active boost).
        return self._system is not None

    @property
    def unique_id(self) -> str:
        tail = "boost" if self._start else "boost_cancel"
        return f"{DOMAIN}_scf_{self.system_id}_dhw_{self._index}_{tail}"

    @property
    def name(self) -> str:
        base = f"Warmwasser {self._index} Einmalladung"
        return base if self._start else f"{base} abbrechen"

    @property
    def extra_state_attributes(self):
        return {
            "boost_active": self._state_value("activeOperation") == "BOOST_ACTIVE",
            "boost_possible": bool(self._state_value("isBoostPossible")),
        }

    @property
    def device_info(self) -> DeviceInfo:
        system = self._system
        return DeviceInfo(
            identifiers={(DOMAIN, f"scf_{self.system_id}")},
            name=system.home_name if system else "Vaillant iQconnect",
            manufacturer="Vaillant",
            model=system.nomenclature if system else "iQconnect",
        )

    async def async_press(self) -> None:
        await call_boost(self.coordinator.api, self.system_id, self._index, self._start)
        await self.coordinator.async_request_refresh()
