"""Zahlenwerte: Lautstärke, Zyklen, Laufzeiten, Zieltemperatur, Cube-Haltbarkeit."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity


@dataclass(frozen=True, kw_only=True)
class W7HNumberDescription(NumberEntityDescription):
    setting: str
    scale: float = 1  # Gerätewert = Anzeige * scale
    local: bool = False  # nur bei uns gespeichert, nicht ans Gerät
    needs_heater: bool = False


NUMBERS: tuple[W7HNumberDescription, ...] = (
    W7HNumberDescription(key="volume", setting="volume", native_min_value=0, native_max_value=100,
                         native_step=1, mode=NumberMode.SLIDER, entity_category=EntityCategory.CONFIG,
                         icon="mdi:volume-high"),
    W7HNumberDescription(key="fountain_time", setting="fountainTime", native_min_value=1, native_max_value=60,
                         native_step=1, native_unit_of_measurement=UnitOfTime.MINUTES,
                         entity_category=EntityCategory.CONFIG, icon="mdi:timer-outline"),
    W7HNumberDescription(key="sleep_time", setting="sleepTime", native_min_value=1, native_max_value=60,
                         native_step=1, native_unit_of_measurement=UnitOfTime.MINUTES,
                         entity_category=EntityCategory.CONFIG, icon="mdi:sleep"),
    W7HNumberDescription(key="water_change_cycle", setting="waterChangeCycle", native_min_value=1,
                         native_max_value=30, native_step=1, native_unit_of_measurement=UnitOfTime.DAYS,
                         entity_category=EntityCategory.CONFIG, icon="mdi:calendar-refresh"),
    W7HNumberDescription(key="flush_cycle", setting="flushCycle", native_min_value=1, native_max_value=30,
                         native_step=1, native_unit_of_measurement=UnitOfTime.DAYS,
                         entity_category=EntityCategory.CONFIG, icon="mdi:repeat"),
    W7HNumberDescription(key="add_water_mode", setting="addWaterMode", native_min_value=0, native_max_value=5,
                         native_step=1, entity_category=EntityCategory.CONFIG, icon="mdi:water-sync"),
    W7HNumberDescription(key="target_temp", setting="targetTemp", scale=10, native_min_value=20,
                         native_max_value=30, native_step=1, device_class=NumberDeviceClass.TEMPERATURE,
                         native_unit_of_measurement=UnitOfTemperature.CELSIUS, needs_heater=True),
    W7HNumberDescription(key="move_sensitivity", setting="moveSensitivity", native_min_value=0,
                         native_max_value=10, native_step=1, entity_category=EntityCategory.CONFIG,
                         entity_registry_enabled_default=False),
    W7HNumberDescription(key="detect_interval", setting="detectInterval", native_min_value=0,
                         native_max_value=3600, native_step=1, native_unit_of_measurement=UnitOfTime.SECONDS,
                         entity_category=EntityCategory.CONFIG, entity_registry_enabled_default=False),
    W7HNumberDescription(key="selected_sound", setting="selectedSound", native_min_value=-1,
                         native_max_value=100, native_step=1, entity_category=EntityCategory.CONFIG,
                         entity_registry_enabled_default=False),
    W7HNumberDescription(key="cube_durability", setting="cube_durability", local=True, native_min_value=1,
                         native_max_value=90, native_step=1, native_unit_of_measurement=UnitOfTime.DAYS,
                         entity_category=EntityCategory.CONFIG, icon="mdi:cube-outline"),
)


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HNumber(device, desc) for desc in NUMBERS)


class W7HNumber(W7HEntity, NumberEntity):
    entity_description: W7HNumberDescription

    def __init__(self, device: W7HDevice, description: W7HNumberDescription) -> None:
        super().__init__(device, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        desc = self.entity_description
        if desc.local:
            return True
        if desc.needs_heater and not self.device.state.get("heatInstall"):
            return False
        return super().available and desc.setting in self.device.settings

    @property
    def native_value(self) -> float | None:
        desc = self.entity_description
        source = self.device.local if desc.local else self.device.settings
        value = source.get(desc.setting)
        return None if value is None else value / desc.scale

    async def async_set_native_value(self, value: float) -> None:
        desc = self.entity_description
        raw = int(round(value * desc.scale))
        if desc.local:
            self.device.async_set_local({desc.setting: raw})
        else:
            await self.device.async_set_settings({desc.setting: raw})
