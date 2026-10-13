"""Buttons für die Aktionen des Brunnens."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import W7HConfigEntry
from .const import ACTION_DEEP_CLEAN, ACTION_DRAIN, ACTION_DRAIN_AND_FLUSH, ACTION_REFILL
from .device import W7HDevice
from .entity import W7HEntity


async def _start(dev: W7HDevice, action: int) -> None:
    dev.async_set_local({"last_start_action": action})
    await dev.async_invoke("start", {"start_action": action})


async def _stop(dev: W7HDevice) -> None:
    await dev.async_invoke("stop", {"stop_action": dev.local.get("last_start_action", 0)})


async def _continue(dev: W7HDevice) -> None:
    await dev.async_invoke("continue", {"continue_action": dev.local.get("last_start_action", 0)})


async def _discern(dev: W7HDevice) -> None:
    """Tierfotos neu einlesen und den Brunnen sie neu holen lassen."""
    if dev.pets is not None:
        await dev.pets.async_scan()
    await dev.async_invoke("discern", {"discern_get": 1})


async def _cube_reset(dev: W7HDevice) -> None:
    dev.async_set_local({"cube_reset_at": time.time()})


@dataclass(frozen=True, kw_only=True)
class W7HButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[W7HDevice], Awaitable[None]]
    local: bool = False


BUTTONS: tuple[W7HButtonDescription, ...] = (
    W7HButtonDescription(key="refill", icon="mdi:water-plus", press_fn=lambda d: _start(d, ACTION_REFILL)),
    W7HButtonDescription(key="drain", icon="mdi:water-minus", press_fn=lambda d: _start(d, ACTION_DRAIN)),
    W7HButtonDescription(key="drain_and_flush", icon="mdi:water-sync",
                         press_fn=lambda d: _start(d, ACTION_DRAIN_AND_FLUSH)),
    W7HButtonDescription(key="deep_clean", icon="mdi:kettle-steam",
                         press_fn=lambda d: _start(d, ACTION_DEEP_CLEAN)),
    W7HButtonDescription(key="stop", icon="mdi:stop", press_fn=_stop),
    W7HButtonDescription(key="continue", icon="mdi:play", press_fn=_continue),
    W7HButtonDescription(key="clear_cwt_light", icon="mdi:lightbulb-off-outline",
                         press_fn=lambda d: d.async_invoke("clear_cwt_light")),
    W7HButtonDescription(key="add_water_reset", icon="mdi:water-check",
                         press_fn=lambda d: d.async_invoke("add_water_Reset")),
    W7HButtonDescription(key="cube_reset", icon="mdi:cube-send", press_fn=_cube_reset, local=True),
    W7HButtonDescription(key="discern_refresh", icon="mdi:cat", entity_category=EntityCategory.CONFIG,
                         press_fn=_discern),
    W7HButtonDescription(key="reset_cycle_pump", icon="mdi:pump", entity_category=EntityCategory.CONFIG,
                         entity_registry_enabled_default=False,
                         press_fn=lambda d: d.async_invoke("reset_cycle_pump")),
    W7HButtonDescription(key="reset_lift_valve", icon="mdi:valve", entity_category=EntityCategory.CONFIG,
                         entity_registry_enabled_default=False,
                         press_fn=lambda d: d.async_invoke("reset_lift_valve")),
)


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities(W7HButton(device, desc) for desc in BUTTONS)


class W7HButton(W7HEntity, ButtonEntity):
    entity_description: W7HButtonDescription

    def __init__(self, device: W7HDevice, description: W7HButtonDescription) -> None:
        super().__init__(device, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return True if self.entity_description.local else super().available

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.device)
