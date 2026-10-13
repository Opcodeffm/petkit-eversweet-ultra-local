"""Auswahlfelder: Fließmodus und Spülintensität."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity

# Einstellung → (Entity-Schlüssel, {Gerätewert: Option}, Kategorie)
SELECTS = {
    "fountainMode": ("fountain_mode", {0: "off", 1: "continuous", 2: "interval", 3: "sensor"}, None),
    "flushIntensity": ("flush_intensity", {1: "low", 2: "medium", 3: "high"}, EntityCategory.CONFIG),
}


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HSelect(device, setting, *meta) for setting, meta in SELECTS.items())


class W7HSelect(W7HEntity, SelectEntity):
    def __init__(self, device: W7HDevice, setting: str, key: str, mapping: dict[int, str],
                 category: EntityCategory | None) -> None:
        super().__init__(device, key)
        self._setting = setting
        self._mapping = mapping
        self._attr_options = list(mapping.values())
        self._attr_entity_category = category

    @property
    def available(self) -> bool:
        return super().available and self._setting in self.device.settings

    @property
    def current_option(self) -> str | None:
        return self._mapping.get(self.device.settings.get(self._setting))

    async def async_select_option(self, option: str) -> None:
        value = next(k for k, v in self._mapping.items() if v == option)
        await self.device.async_set_settings({self._setting: value})
