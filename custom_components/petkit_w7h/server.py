"""HTTP-Server, der dem Brunnen die Petkit-Geräte-API lokal nachspielt.

Bewusst: keine MQTT-Zugangsdaten (nur ein Dummy-"ali"-Block auf 127.0.0.1,
ohne den ctrl nicht online geht), kein OTA. Uploads (Clips) landen über
einen nachgebauten Objektspeicher bei uns (media.py).
Protokoll siehe docs/PROTOCOL.de.md im Repository.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs

from aiohttp import web

from homeassistant.util import dt as dt_util

from .device import W7HDevice
from .media import W7HMedia
from .onboarding import W7HOnboarding
from .pets import W7HPets

_LOGGER = logging.getLogger(__name__)

# Event-Inhalte enthalten teils ungequotete IDs wie {"related_event":1_3000_179…}.
_BARE_TOKEN_RE = re.compile(r'(:\s*)([0-9A-Za-z]+_[0-9A-Za-z_]+)(\s*[,}])')


def _loads_lenient(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_BARE_TOKEN_RE.sub(r'\1"\2"\3', text))


def _extract_json(body: str, key: str) -> tuple[Any, int, int] | None:
    """Findet `key=<JSON>` im Rohbody. Der Brunnen URL-kodiert JSON-Werte nicht,
    deshalb funktioniert parse_qs dafür nicht ('&&' im state-String)."""
    marker = f"{key}="
    start = body.find(marker)
    if start < 0 or (start > 0 and body[start - 1] != "&"):
        return None
    begin = start + len(marker)
    try:
        obj, end = json.JSONDecoder().raw_decode(body, begin)
        return obj, start, end
    except ValueError:
        # Nachsichtiger Versuch bis zum nächsten "&key=" bzw. Ende
        nxt = re.search(r"&[a-z_]+=", body[begin:])
        raw = (body[begin: begin + nxt.start()] if nxt else body[begin:]).rstrip("&")
        try:
            return _loads_lenient(raw), start, begin + len(raw)
        except ValueError:
            return None


def _utc_offset_hours() -> float:
    offset = dt_util.now().utcoffset()
    return round(offset.total_seconds() / 3600, 2) if offset else 0.0


class W7HServer:
    def __init__(self, device: W7HDevice, port: int, public_base: str, media: W7HMedia, pets: W7HPets,
                 onboarding: W7HOnboarding) -> None:
        self.device = device
        self.port = port
        self.public_base = public_base
        self.media = media
        self.pets = pets
        self.onboarding = onboarding
        self._runner: web.AppRunner | None = None
        self._capacity_checked = False

    async def async_start(self) -> None:
        app = web.Application(client_max_size=256 * 1024)
        # Objektspeicher für Clips (Upload per PUT, Lesen per GET)
        storage = "/oci/p/{token}/n/{ns}/b/{bucket}/o/{obj:.*}"
        app.router.add_route("PUT", storage, self.media.async_handle_put)
        app.router.add_route("POST", storage, self.media.async_handle_put)
        app.router.add_route("GET", "/oci/n/{ns}/b/{bucket}/o/{obj:.*}", self.media.async_handle_get)
        # Referenzfotos für die Tiererkennung (der Brunnen holt sie per wget)
        app.router.add_route("GET", "/pet/{token}/{pet}/{file}", self.pets.async_handle_get)
        # Einrichtung per Ein-Zeilen-Befehl auf dem Brunnen (onboarding.py)
        app.router.add_route("GET", "/setup.sh", self.onboarding.async_handle_script)
        app.router.add_route("POST", "/setup", self.onboarding.async_handle_post)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        _LOGGER.info("W7H-Server läuft auf Port %s (Gerät nutzt %s)", self.port, self.public_base)

    async def async_stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------------

    async def _handle(self, request: web.Request) -> web.Response:
        body = (await request.read()).decode("utf-8", "replace")
        name = request.path.rstrip("/").rsplit("/", 1)[-1]
        form = dict(request.query)
        if body and "state=" not in body and "content=" not in body:
            form.update({k: v[0] for k, v in parse_qs(body).items()})
        try:
            status, payload = self._route(name, form, body)
        except Exception:  # noqa: BLE001 - der Brunnen darf nie eine HTML-Fehlerseite sehen
            _LOGGER.exception("Fehler bei %s", request.path)
            status, payload = 200, {"result": "success"}
        if name != "heartbeat":
            _LOGGER.debug("%s %s -> %s", request.method, request.path, status)
        return web.json_response(payload, status=status, dumps=lambda o: json.dumps(o, separators=(",", ":")))

    def _ident(self, form: dict[str, str]) -> dict[str, Any]:
        dev = self.device
        return {
            "id": dev.device_id,
            "mac": form.get("mac") or dev.identity.get("mac", ""),
            "sn": form.get("sn") or dev.identity.get("sn", ""),
            "secret": dev.secret,
            # Zeitzone von Home Assistant (inkl. Sommerzeit), nicht das Echo des Geräts
            "timezone": _utc_offset_hours(),
            "locale": dev.hass.config.time_zone,
        }

    def _route(self, name: str, form: dict[str, str], body: str) -> tuple[int, Any]:
        dev = self.device

        if name == "heartbeat":
            dev.async_seen()
            if not self._capacity_checked and self.media.enabled:
                self._capacity_checked = True
                dev.async_ensure_capacity()
            items = dev.pop_heartbeat_items()
            # Mit Befehlen NUR Einträge mit timestamp – sonst stürzt ctrl beim Sortieren ab.
            return 200, {"result": items or [{"time": int(time.time() * 1000)}]}

        if name == "dev_signup":
            dev.async_update_identity({k: form.get(k) for k in ("mac", "sn", "bt_mac", "firmware", "locale", "timezone")})
            dev.async_seen()
            return 200, {"result": self._ident(form)}

        if name in ("dev_syncTime", "dev_sync_time"):
            return 200, {"result": int(time.time())}

        if name in ("dev_iot_device_info", "dev_only_iot_device_info", "dev_only_iot_device_info_v2"):
            return 200, {"result": {"ali": {
                "id": dev.device_id, "deviceName": "local", "deviceSecret": "local",
                "iotPlatform": "ALI", "iotInstanceId": "local", "productKey": "local",
                "mqttHost": "127.0.0.1", "createdAt": 1791300000000, "type": 1, "regionId": "local",
            }}}

        if name == "dev_serverinfo":
            return 200, {"result": {"ipServers": [], "dns": [], "apiServers": [self.public_base],
                                    "nextTick": 3600, "linked": 1}}

        if name == "dev_device_info":
            return 200, {"result": {
                **self._ident(form), "signupAt": "2026-10-06T14:00:00.000+0000",
                "modelCode": 0, "familyId": 0, "btMac": dev.identity.get("bt_mac", ""),
                "settings": dev.settings, "userId": "0",
                "capacity": dev.capacity_for_device() if self.media.enabled else dev.capacity,
                "cloudProduct": [],
            }}

        if name == "dev_state_report":
            found = _extract_json(body, "state")
            if found and isinstance(found[0], dict):
                dev.async_update_state(found[0])
            now = dt_util.now()
            stamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + now.strftime("%z")
            return 200, {"result": {"interval": 3600, "time": stamp}}

        if name == "dev_event_report":
            self._parse_event(body)
            return 200, {"result": "success"}

        if name == "dev_upload_file_info_v2":
            found = _extract_json(body, "fileInfos")
            infos = found[0] if found else _loads_lenient(form.get("fileInfos") or "[]")
            if isinstance(infos, list):
                self.media.async_file_infos(infos)
            return 200, {"result": []}
        if name == "dev_oss_sts_info_new_v2":
            return 200, {"result": self.media.capability()}
        if name in ("dev_ota_complete", "dev_ota_start"):
            return 200, {"result": "success"}
        if name == "dev_video_device_info":
            return 200, {"result": {"agora": {"license": "", "appId": ""}}}
        if name in ("dev_ota_check", "dev_sound_get", "dev_multi_config"):
            return 200, {"result": {}}
        if name == "dev_upload_log_token":
            return 200, {"result": {"type": "ali", "data": {"token": "", "bucketName": "", "pathPrefix": "",
                                                            "endPoint": "", "secret": "", "keyId": ""}}}
        if name == "dev_ble_device":
            return 200, {"result": {"list": [], "nextTick": 3600}}
        if name == "dev_schedule_get":
            return 200, {"result": []}
        if name == "dev_attire_over":
            return 200, {"result": [0]}
        if name == "dev_discern_config":
            return 200, {"result": {"list": {"area": 6000, "score": 25.0}}}
        if name == "dev_discern_pic":
            return 200, {"result": self.pets.discern_list()}

        _LOGGER.info("Unbekannter Endpunkt vom Brunnen: %s (Body %d Bytes)", name, len(body))
        return 404, {}

    def _parse_event(self, body: str) -> None:
        state = _extract_json(body, "state")
        head = body[: state[1]] if state else body
        content = _extract_json(head, "content")
        if content:
            head = head[: content[1]]
        fields = {k: v[0] for k, v in parse_qs(head.rstrip("&")).items()}
        try:
            event_type = int(fields.get("event_type", "-1"))
        except ValueError:
            event_type = -1
        self.device.async_handle_event(
            event_type,
            fields.get("event_id", ""),
            content[0] if content and isinstance(content[0], dict) else None,
            state[0] if state and isinstance(state[0], dict) else None,
        )
