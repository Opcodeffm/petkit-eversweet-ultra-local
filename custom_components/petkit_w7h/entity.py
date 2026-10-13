"""Gemeinsame Basis für alle Entities des Brunnens."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .device import W7HDevice

_IP_RE = re.compile(r'"?Ip"?:\\?"?(\d{1,3}(?:\.\d{1,3}){3})')


class W7HEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: W7HDevice, key: str) -> None:
        self.device = device
        self._attr_unique_id = f"{device.device_id}_{key}"
        self._attr_translation_key = key.lower()

    @property
    def device_info(self) -> DeviceInfo:
        ident = self.device.identity
        return DeviceInfo(
            identifiers={(DOMAIN, str(self.device.device_id))},
            name="Eversweet Ultra",
            manufacturer="PETKIT",
            model="Eversweet Ultra (W7H)",
            serial_number=ident.get("sn"),
            sw_version=ident.get("firmware"),
        )

    @property
    def available(self) -> bool:
        return self.device.online

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.device.async_add_listener(self.async_write_ha_state))


def state_value(device: W7HDevice, path: str) -> Any:
    """Wert aus dem letzten Status, Pfad mit Punkten, z. B. 'wifi.rsq'."""
    node: Any = device.state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def ip_address(device: W7HDevice) -> str | None:
    match = _IP_RE.search(str(device.state.get("other", "")))
    return match.group(1) if match else None
