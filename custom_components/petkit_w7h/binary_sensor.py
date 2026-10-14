"""Binäre Sensoren: Verbindung, Tanks, Einbauzustände und Fehler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .const import ERROR_FLAGS
from .device import W7HDevice
from .entity import W7HEntity, state_value


@dataclass(frozen=True, kw_only=True)
class W7HBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[W7HDevice], bool | None]
    attrs_fn: Callable[[W7HDevice], dict[str, Any]] | None = None


def _flag(field: str) -> Callable[[W7HDevice], bool | None]:
    def fn(dev: W7HDevice) -> bool | None:
        value = state_value(dev, field)
        return None if value is None else bool(value)
    return fn


def _is(status: str | None, *values: str) -> bool | None:
    return None if status in (None, "unknown") else status in values


def _any_error(dev: W7HDevice) -> bool | None:
    err = dev.state.get("err")
    return None if not isinstance(err, dict) else any(err.get(f) for f in ERROR_FLAGS)


BINARY_SENSORS: tuple[W7HBinaryDescription, ...] = (
    W7HBinaryDescription(key="connected", device_class=BinarySensorDeviceClass.CONNECTIVITY,
                         entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: d.online),
    W7HBinaryDescription(key="problem", device_class=BinarySensorDeviceClass.PROBLEM, value_fn=_any_error,
                         attrs_fn=lambda d: {f: bool((d.state.get("err") or {}).get(f)) for f in ERROR_FLAGS}),
    W7HBinaryDescription(key="drinking", value_fn=lambda d: d.drinking, icon="mdi:cat"),
    # Für Automationen: Tankzustände als eigene Sensoren (Klartext-Zustände in den Übersetzungen)
    W7HBinaryDescription(key="waste_tank_full", device_class=BinarySensorDeviceClass.PROBLEM,
                         value_fn=lambda d: _is(d.waste_tank_status(), "full"), icon="mdi:delete-alert"),
    W7HBinaryDescription(key="clean_water_low", device_class=BinarySensorDeviceClass.PROBLEM,
                         value_fn=lambda d: _is(d.clean_tank_status(), "low", "empty"), icon="mdi:water-alert"),
    W7HBinaryDescription(key="clean_water_empty", device_class=BinarySensorDeviceClass.PROBLEM,
                         value_fn=lambda d: _is(d.clean_tank_status(), "empty"), icon="mdi:water-off"),
    W7HBinaryDescription(key="waste_tank_installed", value_fn=_flag("wtInstall"), icon="mdi:delete-variant"),
    W7HBinaryDescription(key="waste_tank_locked", device_class=BinarySensorDeviceClass.LOCK,
                         value_fn=lambda d: None if state_value(d, "wtLock") is None else not state_value(d, "wtLock")),
    W7HBinaryDescription(key="tray_installed", value_fn=_flag("stgInstall"), icon="mdi:tray"),
    W7HBinaryDescription(key="heater_installed", value_fn=_flag("heatInstall"), icon="mdi:radiator",
                         entity_category=EntityCategory.DIAGNOSTIC),
    W7HBinaryDescription(key="add_water_frequent", device_class=BinarySensorDeviceClass.PROBLEM,
                         value_fn=_flag("addWaterFrequent")),
    *(
        W7HBinaryDescription(key=f"err_{flag}", device_class=BinarySensorDeviceClass.PROBLEM,
                             entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
                             value_fn=_flag(f"err.{flag}"))
        for flag in ERROR_FLAGS
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HBinarySensor(device, desc) for desc in BINARY_SENSORS)


class W7HBinarySensor(W7HEntity, BinarySensorEntity):
    entity_description: W7HBinaryDescription

    def __init__(self, device: W7HDevice, description: W7HBinaryDescription) -> None:
        super().__init__(device, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return True if self.entity_description.key == "connected" else super().available

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.device) if fn else None
