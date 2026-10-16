"""Petkit Eversweet Ultra (W7H) – lokal, ohne Cloud und ohne MQTT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path

from aiohttp import web
import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval

from .config_flow import default_message_language, default_pet_name
from .const import (
    CONF_CLIPS,
    CONF_DEVICE_ID,
    CONF_DEVICE_SECRET,
    CONF_DRINK_MERGE_MIN,
    CONF_MESSAGE_LANGUAGE,
    CONF_MAX_STORAGE_GB,
    CONF_ONLY_KNOWN,
    CONF_PET_NAME,
    CONF_PORT,
    CONF_PUBLIC_HOST,
    CONF_RETENTION_DAYS,
    CONF_RETENTION_OTHER_DAYS,
    DEFAULT_DRINK_MERGE_MIN,
    DEFAULT_MAX_STORAGE_GB,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RETENTION_OTHER_DAYS,
    DOMAIN,
    MEDIA_URL,
    PLATFORMS,
    STATIC_URL,
)
from .device import W7HDevice
from .media import W7HMedia, media_root
from .onboarding import W7HOnboarding
from .pets import W7HPets, split_names
from .server import W7HServer

# So oft schauen wir nach neuen Tierfotos im Medienordner
PET_SCAN_INTERVAL = timedelta(minutes=10)


@dataclass
class W7HRuntime:
    device: W7HDevice
    server: W7HServer
    media: W7HMedia
    pets: W7HPets
    # Entities erst, wenn der Brunnen registriert ist (sonst entstünden sie mit Geräte-ID 0)
    platforms_loaded: bool = False


type W7HConfigEntry = ConfigEntry[W7HRuntime]

SERVICE_INVOKE = "invoke"
SERVICE_SET_SETTINGS = "set_settings"
SERVICE_IGNORE_DRINK = "ignore_drink"
CARD_FILE = "petkit-drink-card.js"


async def async_setup_entry(hass: HomeAssistant, entry: W7HConfigEntry) -> bool:
    data = entry.data
    options = entry.options
    # Ohne ID/Secret wartet die Integration auf die Einrichtung per setup.sh (onboarding.py).
    device = W7HDevice(hass, entry.entry_id, int(data.get(CONF_DEVICE_ID) or 0), data.get(CONF_DEVICE_SECRET) or "")
    await device.async_load()
    device.message_language = options.get(CONF_MESSAGE_LANGUAGE) or default_message_language(hass)
    names = split_names(options.get(CONF_PET_NAME)) or [default_pet_name(device.message_language)]
    device.pet_name = names[0]
    device.only_known = bool(options.get(CONF_ONLY_KNOWN, False))
    device.merge_gap_s = int(options.get(CONF_DRINK_MERGE_MIN, DEFAULT_DRINK_MERGE_MIN)) * 60

    base = f"http://{data[CONF_PUBLIC_HOST]}:{data[CONF_PORT]}"
    media = W7HMedia(hass, device, base)
    media.enabled = options.get(CONF_CLIPS, True)
    pets = W7HPets(hass, device, base, names)
    device.pets = pets
    if await pets.async_scan():
        # Brunnen soll die Fotos neu holen (kommt mit dem nächsten Heartbeat an)
        await device.async_invoke("discern", {"discern_get": 1})
    onboarding = W7HOnboarding(hass, entry, base)
    server = W7HServer(device, int(data[CONF_PORT]), f"{base}/6/", media, pets, onboarding)
    await server.async_start()
    onboarding.async_update_notification()
    entry.runtime_data = W7HRuntime(device, server, media, pets, platforms_loaded=not onboarding.pending)

    await _async_setup_frontend(hass)
    if entry.runtime_data.platforms_loaded:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)

    drink_days = int(options.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
    other_days = int(options.get(CONF_RETENTION_OTHER_DAYS, DEFAULT_RETENTION_OTHER_DAYS))
    max_bytes = int(float(options.get(CONF_MAX_STORAGE_GB, DEFAULT_MAX_STORAGE_GB)) * 1e9)

    async def _cleanup(_now: object = None) -> None:
        await media.async_cleanup(drink_days, other_days, max_bytes)

    # stündlich: hält die Obergrenze ein und aktualisiert den Speicher-Sensor
    entry.async_on_unload(async_track_time_change(hass, _cleanup, minute=17, second=0))

    async def _rescan_pets(_now: object = None) -> None:
        if await pets.async_scan():
            await device.async_invoke("discern", {"discern_get": 1})
            device.async_set_local({})  # Entities neu zeichnen (Namen)

    entry.async_on_unload(async_track_time_interval(hass, _rescan_pets, PET_SCAN_INTERVAL))
    entry.async_create_background_task(hass, _cleanup(), "petkit_w7h_cleanup")
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    device.async_start()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: W7HConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: W7HConfigEntry) -> bool:
    ok = True
    if entry.runtime_data.platforms_loaded:
        ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.server.async_stop()
        await entry.runtime_data.device.async_unload()
    return ok


# --- Clips im Browser: Dateien (mit Anmeldung) und Dashboard-Karte -------------


class W7HMediaView(HomeAssistantView):
    """Liefert Clips und Vorschaubilder aus – nur für angemeldete Nutzer
    (die Karte holt sich dafür signierte Links)."""

    url = MEDIA_URL + "/{path:.+}"
    name = f"api:{DOMAIN}:media"
    requires_auth = True

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        target = (self.root / path).resolve()
        if (self.root not in target.parents or any(p.startswith(".") for p in target.relative_to(self.root).parts)
                or target.suffix.lower() not in (".mp4", ".jpg")):
            return web.Response(status=404)
        exists = await request.app["hass"].async_add_executor_job(target.is_file)
        if not exists:
            return web.Response(status=404)
        # Clips können nachträglich länger werden: immer nachfragen (ETag macht das billig).
        return web.FileResponse(target, headers={"Cache-Control": "private, no-cache"})


async def _async_setup_frontend(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True
    hass.http.register_view(W7HMediaView(media_root(hass)))
    www = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(www), cache_headers=False)])
    manifest = await hass.async_add_executor_job((Path(__file__).parent / "manifest.json").read_text)
    version = json.loads(manifest).get("version", "0")
    add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={version}")


# --- Dienste --------------------------------------------------------------------


def _device(hass: HomeAssistant) -> W7HDevice:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise vol.Invalid("Petkit W7H ist nicht eingerichtet")
    return entries[0].runtime_data.device


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_INVOKE):
        return

    async def invoke(call: ServiceCall) -> None:
        await _device(hass).async_invoke(call.data["service"], call.data.get("payload") or {})

    async def set_settings(call: ServiceCall) -> None:
        await _device(hass).async_set_settings(call.data["settings"])

    hass.services.async_register(
        DOMAIN, SERVICE_INVOKE, invoke,
        schema=vol.Schema({vol.Required("service"): cv.string, vol.Optional("payload"): dict}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETTINGS, set_settings,
        schema=vol.Schema({vol.Required("settings"): dict}),
    )

    async def ignore_drink(call: ServiceCall) -> None:
        _device(hass).async_ignore_drinks(call.data["event_id"], call.data["ignore"])

    hass.services.async_register(
        DOMAIN, SERVICE_IGNORE_DRINK, ignore_drink,
        schema=vol.Schema({
            vol.Required("event_id"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("ignore", default=True): cv.boolean,
        }),
    )
