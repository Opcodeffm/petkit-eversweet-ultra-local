"""Livebild der Brunnen-Kamera.

Den Stream liefert `tserver`, ein Testprogramm aus der Firmware. Der Brunnen startet es nur mit
unserem Boot-Haken (/opt/w7hlocal/live_stream.sh). Es sendet H.264 mit AAC als HTTP-FLV auf
Port 80, ohne Anmeldung. Home Assistant (go2rtc bzw. stream) holt das Bild von dort.
Läuft `tserver` nicht, ist die Entität nicht verfügbar.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import time
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import W7HConfigEntry
from .device import W7HDevice
from .entity import W7HEntity, ip_address

STREAM_PORT = 80
STREAM_PATH = "/main.flv?audio=1"
PROBE_INTERVAL = timedelta(seconds=60)
SNAPSHOT_MAX_AGE_S = 10


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([W7HCamera(entry.runtime_data.device)])


class W7HCamera(W7HEntity, Camera):
    _attr_supported_features = CameraEntityFeature.STREAM
    # Braucht den tserver-Start auf dem Brunnen, deshalb bei neuen Installationen erst einmal aus.
    _attr_entity_registry_enabled_default = False

    def __init__(self, device: W7HDevice) -> None:
        W7HEntity.__init__(self, device, "live")
        Camera.__init__(self)
        self._reachable = False
        self._image: bytes | None = None
        self._image_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.device.online and self._reachable

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._async_probe, PROBE_INTERVAL))
        await self._async_probe()

    async def _async_probe(self, _now: Any = None) -> None:
        """Nur prüfen, ob auf dem Brunnen jemand auf Port 80 lauscht (kein Stream-Abruf)."""
        ip = ip_address(self.device)
        ok = False
        if ip:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(ip, STREAM_PORT), 3)
                writer.close()
                await writer.wait_closed()
                ok = True
            except (OSError, TimeoutError):
                ok = False
        if ok != self._reachable:
            self._reachable = ok
            self.async_write_ha_state()

    async def stream_source(self) -> str | None:
        ip = ip_address(self.device)
        return f"http://{ip}{STREAM_PATH}" if ip else None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Standbild per ffmpeg aus dem Stream, höchstens alle 10 s neu (Vorschaubilder im Dashboard)."""
        async with self._lock:
            if self._image and time.monotonic() - self._image_at < SNAPSHOT_MAX_AGE_S:
                return self._image
            source = await self.stream_source()
            if not source or not self.available:
                return self._image
            image = await async_get_image(self.hass, source, width=width, height=height)
            if image:
                self._image, self._image_at = image, time.monotonic()
            return self._image
