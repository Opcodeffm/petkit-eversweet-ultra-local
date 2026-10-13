"""Uhrzeiten: geplanter Wasserwechsel/Spülen und alle Zeitfenster (Nicht stören usw.)."""

from __future__ import annotations

from datetime import time as dtime

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity

# Sekunden seit Mitternacht
SECOND_TIMES = {"waterChangeTime": "water_change_time", "flushTime": "flush_time"}

# Zeitfenster [[start, ende]] in Minuten seit Mitternacht (erstes Fenster)
RANGES = {
    "distrubMultiRange": "disturb",
    "toneMultiRange": "tone",
    "wlDisturbMultiRange": "wl_disturb",
    "awDisturbMultiRange": "aw_disturb",
    "lightMultiRange": "light",
    "cameraMultiRange": "camera",
    "lightAssistMultiRange": "light_assist",
}


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    entities: list[TimeEntity] = [W7HSecondsTime(device, s, k) for s, k in SECOND_TIMES.items()]
    for setting, key in RANGES.items():
        entities.append(W7HRangeTime(device, setting, f"{key}_start", 0))
        entities.append(W7HRangeTime(device, setting, f"{key}_end", 1))
    async_add_entities(entities)


class _W7HTime(W7HEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: W7HDevice, setting: str, key: str) -> None:
        super().__init__(device, key)
        self._setting = setting

    @property
    def available(self) -> bool:
        return super().available and self._setting in self.device.settings


class W7HSecondsTime(_W7HTime):
    @property
    def native_value(self) -> dtime | None:
        sec = self.device.settings.get(self._setting)
        if sec is None:
            return None
        sec = int(sec) % 86400
        return dtime(sec // 3600, (sec % 3600) // 60, sec % 60)

    async def async_set_value(self, value: dtime) -> None:
        await self.device.async_set_settings(
            {self._setting: value.hour * 3600 + value.minute * 60 + value.second})


class W7HRangeTime(_W7HTime):
    def __init__(self, device: W7HDevice, setting: str, key: str, index: int) -> None:
        super().__init__(device, setting, key)
        self._index = index

    def _range(self) -> list[int] | None:
        ranges = self.device.settings.get(self._setting)
        if isinstance(ranges, list) and ranges and isinstance(ranges[0], list) and len(ranges[0]) == 2:
            return list(ranges[0])
        return None

    @property
    def native_value(self) -> dtime | None:
        rng = self._range()
        if rng is None:
            return None
        minutes = int(rng[self._index])
        if minutes >= 1440:  # Fensterende "24:00"
            return dtime(23, 59)
        return dtime(minutes // 60, minutes % 60)

    async def async_set_value(self, value: dtime) -> None:
        rng = self._range() or [0, 1440]
        minutes = value.hour * 60 + value.minute
        if self._index == 1 and minutes == 1439:
            minutes = 1440  # 23:59 als Fensterende = bis Mitternacht
        rng[self._index] = minutes
        ranges = list(self.device.settings.get(self._setting) or [])
        ranges = [rng] + ranges[1:]
        await self.device.async_set_settings({self._setting: ranges})
