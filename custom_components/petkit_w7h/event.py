"""Event-Entities: Haustier, Fehler und alle übrigen Ereignisse des Brunnens."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity

# Entity-Schlüssel → Ereignisse, die sie auslöst (None = alle übrigen)
EVENT_ENTITIES: dict[str, list[str] | None] = {
    "pet": ["pet_detected", "pet_identified", "drink_start", "drink_over"],
    # Fertiger Trinkvorgang mit Satz ("Mieze hat 55 Sekunden getrunken") bzw. Clip ist da
    "drink": ["drink", "clip_ready"],
    "work": ["work_start", "flush_over", "add_water_over"],
    "fault": ["error_start", "error_over"],
    "other": None,
}
KNOWN = {name for names in EVENT_ENTITIES.values() if names for name in names}


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HEvent(device, key, names) for key, names in EVENT_ENTITIES.items())


class W7HEvent(W7HEntity, EventEntity):
    def __init__(self, device: W7HDevice, key: str, names: list[str] | None) -> None:
        super().__init__(device, f"event_{key}")
        self._names = names
        self._attr_event_types = names or ["other"]

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.device.async_add_event_listener(self._on_event))

    @callback
    def _on_event(self, name: str, data: dict[str, Any]) -> None:
        if self._names is None:
            if name in KNOWN:
                return
            event_type = "other"
        elif name in self._names:
            event_type = name
        else:
            return
        content = data.get("content") or {}
        attrs = {"event_id": data.get("event_id"), **content}
        if data.get("event_type") is not None:
            attrs["event_type_code"] = data["event_type"]
        self._trigger_event(event_type, attrs)
        self.async_write_ha_state()
