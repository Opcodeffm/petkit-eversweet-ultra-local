"""Sensoren: Status, Diagnose, Heizung, Cube und letzte Erkennungen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from . import W7HConfigEntry
from .const import CLEAN_TANK_STATES, WASTE_TANK_STATES, WORK_MODES
from .device import W7HDevice
from .entity import W7HEntity, ip_address, state_value


def _ts(value: Any) -> datetime | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value, timezone.utc) if value > 0 else None


def _errors(dev: W7HDevice) -> str | None:
    if not isinstance(dev.state.get("err"), dict) and not dev.active_errors:
        return None
    texts = dev.error_texts()
    if texts:
        return ", ".join(texts)[:255]
    return "Keine Störung" if dev.message_language == "de" else "No fault"


def _cube_days_left(dev: W7HDevice) -> int | None:
    reset_at = dev.local.get("cube_reset_at")
    if not reset_at:
        return None
    used = int((time.time() - reset_at) // 86400)
    return max(int(dev.local.get("cube_durability", 30)) - used, 0)


@dataclass(frozen=True, kw_only=True)
class W7HSensorDescription(SensorEntityDescription):
    value_fn: Callable[[W7HDevice], Any]
    needs_heater: bool = False


def _code(key: str, field: str, enabled: bool = False) -> W7HSensorDescription:
    """Rohe Zustandscodes des Geräts (Bedeutung teils noch offen) als Diagnose."""
    return W7HSensorDescription(
        key=key, value_fn=lambda d, f=field: state_value(d, f),
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=enabled,
    )


SENSORS: tuple[W7HSensorDescription, ...] = (
    W7HSensorDescription(key="error", value_fn=_errors, icon="mdi:alert-circle-outline"),
    # Gleiche Schlüssel wie früher die Rohcodes, damit die Entity-IDs bleiben.
    W7HSensorDescription(
        key="cwt_state", device_class=SensorDeviceClass.ENUM, options=CLEAN_TANK_STATES,
        value_fn=lambda d: d.clean_tank_status(), icon="mdi:cup-water",
    ),
    W7HSensorDescription(
        key="wt_state", device_class=SensorDeviceClass.ENUM, options=WASTE_TANK_STATES,
        value_fn=lambda d: d.waste_tank_status(), icon="mdi:delete-variant",
    ),
    W7HSensorDescription(
        key="last_pet_detected", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts(d.local.get("last_pet_detected")),
    ),
    W7HSensorDescription(  # letzter gezählter Trinkvorgang (ohne Hantieren/Putzen)
        key="last_drink", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts((d.last_drink or {}).get("start")),
    ),
    W7HSensorDescription(  # Name statt Tier-ID (0 = niemand am Gesicht erkannt)
        key="last_pet_id", icon="mdi:cat",
        value_fn=lambda d: d.recognized_label(d.local.get("last_pet_id")) if d.local.get("last_pet_id") is not None else None,
    ),
    W7HSensorDescription(
        key="last_drink_duration", native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION, value_fn=lambda d: (d.last_drink or {}).get("duration"),
        icon="mdi:cup-water",
    ),
    W7HSensorDescription(  # belegter Platz aller Clips (stündlich beim Aufräumen gemessen)
        key="clip_storage", device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: round(d.local["clip_bytes"] / 1e6, 1) if d.local.get("clip_bytes") is not None else None,
        icon="mdi:harddisk",
    ),
    W7HSensorDescription(
        key="work_mode", device_class=SensorDeviceClass.ENUM, options=list(WORK_MODES.values()),
        value_fn=lambda d: WORK_MODES.get(state_value(d, "workState.workMode") or 0), icon="mdi:cog-play",
    ),
    _code("work_process", "workState.workProcess"),
    _code("work_reason", "workState.workReason"),
    W7HSensorDescription(
        key="cube_days_left", native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=_cube_days_left, icon="mdi:cube-outline",
    ),
    # Heizung (nur mit beheizter Wasserschale)
    W7HSensorDescription(
        key="heat_real_temp", device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: state_value(d, "heatRealTemp"), needs_heater=True,
    ),
    W7HSensorDescription(
        key="heat_left_time", native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION, value_fn=lambda d: state_value(d, "heatLeftTime"), needs_heater=True,
    ),
    W7HSensorDescription(
        key="heat_status_time", native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION, value_fn=lambda d: state_value(d, "heatStatusTime"), needs_heater=True,
    ),
    W7HSensorDescription(
        key="disinfect_time", native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION, value_fn=lambda d: state_value(d, "disinfectTime"),
    ),
    # Diagnose
    W7HSensorDescription(
        key="wifi_rssi", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: state_value(d, "wifi.rsq"),
    ),
    W7HSensorDescription(key="ip_address", entity_category=EntityCategory.DIAGNOSTIC, value_fn=ip_address),
    W7HSensorDescription(
        key="cpu", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: state_value(d, "cpu"),
        entity_registry_enabled_default=False,
    ),
    W7HSensorDescription(
        key="runtime", native_unit_of_measurement=UnitOfTime.SECONDS, device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: state_value(d, "runtime"),
        entity_registry_enabled_default=False,
    ),
    _code("reboot_reason", "reboot_reason"),
    _code("camera_status", "cameraStatus"),
    _code("heat_state", "heatState"),
    _code("lift_valve_state", "liftValveState"),
    _code("pump_state", "pumpState"),
    _code("water_pump_state", "waterPumpState"),
    _code("add_water_state", "addWaterState"),
    _code("flush_state", "flushState"),
    _code("lift_reset_state", "liftResetState"),
    _code("lift_live_state", "liftLiveState"),
    _code("disinfect_state", "disinfectState"),
    _code("stg_full_state", "stgFullState"),
)


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = entry.runtime_data.device
    async_add_entities([
        *(W7HSensor(device, desc) for desc in SENSORS),
        W7HDrinkLogSensor(device),
        W7HDrinksTodaySensor(device, "drinks_today"),
        W7HDrinksTodaySensor(device, "drink_time_today"),
    ])


class W7HSensor(W7HEntity, SensorEntity):
    entity_description: W7HSensorDescription

    def __init__(self, device: W7HDevice, description: W7HSensorDescription) -> None:
        super().__init__(device, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.needs_heater and not self.device.state.get("heatInstall"):
            return False
        # Werte aus dem lokalen Speicher sind auch offline gültig.
        if self.entity_description.key in ("last_pet_detected", "last_pet_id", "cube_days_left", "error",
                                           "last_drink", "last_drink_duration", "clip_storage"):
            return True
        return super().available

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        st = self.device.state
        if key == "error":
            err = st.get("err") if isinstance(st.get("err"), dict) else {}
            return {"codes": [c for c, on in err.items() if on] + self.device.active_errors}
        # Rohwerte der Hall-Sensoren (Diagnose; Bedeutung teils noch offen)
        hall = st.get("sensor") if isinstance(st.get("sensor"), dict) else {}
        if key == "cwt_state":
            return {"code": st.get("cwtState"), "changes": self.device.tank_log("cwtState"),
                    **{k: hall[k] for k in ("hall_CH", "hall_CL", "hall_CKL", "hall_CKR") if k in hall}}
        if key == "wt_state":
            return {"code": st.get("wtState"), "installed": st.get("wtInstall"), "locked": st.get("wtLock"),
                    "changes": self.device.tank_log("wtState"),
                    **{k: hall[k] for k in ("hall_DH", "hall_DKL", "hall_DKR", "hall_TY", "hall_LTU", "hall_LTD")
                       if k in hall}}
        return None


# --- Trinkvorgänge --------------------------------------------------------------

HISTORY_ITEMS = 30


class W7HDrinkLogSensor(W7HEntity, SensorEntity):
    """Letzter Trinkvorgang als Satz ("Mieze hat 55 Sekunden getrunken"),
    dazu Clip, Vorschaubild und die letzten Trinkvorgänge (für die Dashboard-Karte)."""

    _attr_icon = "mdi:cat"
    _unrecorded_attributes = frozenset({"history", "clip", "thumb"})

    def __init__(self, device: W7HDevice) -> None:
        super().__init__(device, "drink_log")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        drink = self.device.last_drink
        return self.device.drink_message(drink) if drink else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        drink = dev.last_drink
        today = dev.drinks_on(dt_util.now().date())
        attrs: dict[str, Any] = dev.drink_view(drink) if drink else {}
        attrs.pop("message", None)
        attrs["today_count"] = len(today)
        attrs["today_seconds"] = sum(d.get("duration") or 0 for d in today)
        attrs["history"] = [dev.drink_view(d) for d in reversed(dev.visible_drinks()[-HISTORY_ITEMS:])]
        return attrs


class W7HDrinksTodaySensor(W7HEntity, SensorEntity):
    """Wie oft bzw. wie lange heute getrunken wurde (setzt sich um Mitternacht zurück)."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device: W7HDevice, key: str) -> None:
        super().__init__(device, key)
        if key == "drink_time_today":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_icon = "mdi:timer-outline"
        else:
            self._attr_icon = "mdi:cup-water"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        today = self.device.drinks_on(dt_util.now().date())
        if self._attr_translation_key == "drink_time_today":
            return sum(d.get("duration") or 0 for d in today)
        return len(today)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _midnight(_now: Any) -> None:
            self.async_write_ha_state()

        self.async_on_remove(async_track_time_change(self.hass, _midnight, hour=0, minute=0, second=1))
