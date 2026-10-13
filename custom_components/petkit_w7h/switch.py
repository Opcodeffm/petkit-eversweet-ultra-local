"""Schalter für alle Ein/Aus-Einstellungen des Brunnens."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity

# Einstellung → (Entity-Schlüssel, Kategorie, standardmäßig aktiv)
SWITCHES: dict[str, tuple[str, EntityCategory | None, bool]] = {
    "camera": ("camera", None, True),
    "microphone": ("microphone", None, True),
    "night": ("night_vision", EntityCategory.CONFIG, True),
    "cameraLight": ("camera_light", EntityCategory.CONFIG, True),
    "microLight": ("micro_light", EntityCategory.CONFIG, True),
    "lightMode": ("light_mode", EntityCategory.CONFIG, True),
    "manualLock": ("manual_lock", None, True),
    "addWaterSwitch": ("add_water_switch", None, True),
    "autoWaterChange": ("auto_water_change", None, True),
    "autoFlush": ("auto_flush", None, True),
    "heaterSwitch": ("heater_switch", None, True),
    "petDetection": ("pet_detection", EntityCategory.CONFIG, True),
    "drinkDetection": ("drink_detection", EntityCategory.CONFIG, True),
    "vomitDetection": ("vomit_detection", EntityCategory.CONFIG, True),
    "moveDetection": ("move_detection", EntityCategory.CONFIG, False),
    "smartFrame": ("smart_frame", EntityCategory.CONFIG, True),
    "soundEnable": ("sound_enable", EntityCategory.CONFIG, True),
    "systemSoundEnable": ("system_sound_enable", EntityCategory.CONFIG, True),
    "voice": ("voice", EntityCategory.CONFIG, False),
    "disturbMode": ("disturb_mode", EntityCategory.CONFIG, True),
    "toneMode": ("tone_mode", EntityCategory.CONFIG, True),
    "wlDisturbMode": ("wl_disturb_mode", EntityCategory.CONFIG, True),
    "awDisturbMode": ("aw_disturb_mode", EntityCategory.CONFIG, True),
    "cleanWaterLackLight": ("clean_water_lack_light", EntityCategory.CONFIG, True),
    "cleanWaterEmptyLight": ("clean_water_empty_light", EntityCategory.CONFIG, True),
    "wasteWaterFullLight": ("waste_water_full_light", EntityCategory.CONFIG, True),
    "lightAssist": ("light_assist", EntityCategory.CONFIG, False),
    "wifiLightAssist": ("wifi_light_assist", EntityCategory.CONFIG, False),
    "timestamp_enable": ("timestamp_enable", EntityCategory.CONFIG, False),
    "preLive": ("pre_live", EntityCategory.CONFIG, False),
    "upload": ("upload", EntityCategory.CONFIG, False),
    "log_upload": ("log_upload", EntityCategory.CONFIG, False),
}


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HSettingSwitch(device, setting, *meta) for setting, meta in SWITCHES.items())


class W7HSettingSwitch(W7HEntity, SwitchEntity):
    def __init__(self, device: W7HDevice, setting: str, key: str,
                 category: EntityCategory | None, enabled: bool) -> None:
        super().__init__(device, key)
        self._setting = setting
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled

    @property
    def available(self) -> bool:
        if self._setting == "heaterSwitch" and not self.device.state.get("heatInstall"):
            return False
        return super().available and self._setting in self.device.settings

    @property
    def is_on(self) -> bool | None:
        value = self.device.settings.get(self._setting)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.device.async_set_settings({self._setting: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_set_settings({self._setting: 0})
