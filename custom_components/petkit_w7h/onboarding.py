"""Einrichtung per Ein-Zeilen-Befehl auf dem Brunnen.

Ist die Integration ohne Geräte-ID und Secret angelegt, liefert unser Server unter
`/setup.sh` ein Skript (setup/w7h_setup.sh mit eingesetzter HA-Adresse). Auf dem
Brunnen als root ausgeführt, liest es ID und Secret aus /opt/user.conf, schickt sie per
POST an `/setup`, stellt den Brunnen auf diesen Server um und startet ihn neu.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import CONF_DEVICE_ID, CONF_DEVICE_SECRET, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCRIPT = Path(__file__).parent / "setup" / "w7h_setup.sh"
NOTIFICATION_ID = f"{DOMAIN}_setup"
_SECRET_RE = re.compile(r"[A-Za-z0-9._~+/=-]{8,128}")


def setup_command(base_url: str) -> str:
    return f"wget -qO- {base_url}/setup.sh | sh"


class W7HOnboarding:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, base_url: str) -> None:
        self.hass = hass
        self.entry = entry
        self.base_url = base_url.rstrip("/")

    @property
    def pending(self) -> bool:
        return not self.entry.data.get(CONF_DEVICE_ID)

    @callback
    def async_update_notification(self) -> None:
        """Solange der Brunnen fehlt, steht der Befehl in den Benachrichtigungen."""
        if not self.pending:
            persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID)
            return
        cmd = setup_command(self.base_url)
        if (self.hass.config.language or "").startswith("de"):
            title = "Eversweet Ultra verbinden"
            message = (
                "Melde dich einmal per Telnet als root am Brunnen an und führe diese Zeile aus:\n\n"
                f"`{cmd}`\n\n"
                "Das Skript sichert die Original-Konfiguration, gibt Geräte-ID und Secret an Home "
                "Assistant, stellt den Brunnen um und startet ihn neu. Vorher nur prüfen: "
                f"`{cmd} -s -- --dry-run`. Zurück zur Petkit-Cloud: `{cmd} -s -- --cloud`."
            )
        else:
            title = "Connect the Eversweet Ultra"
            message = (
                "Log in to the fountain once via telnet as root and run:\n\n"
                f"`{cmd}`\n\n"
                "The script backs up the original configuration, hands device ID and secret to Home "
                "Assistant, points the fountain to it and reboots. Check only: "
                f"`{cmd} -s -- --dry-run`. Back to the Petkit cloud: `{cmd} -s -- --cloud`."
            )
        persistent_notification.async_create(self.hass, message, title, NOTIFICATION_ID)

    async def async_handle_script(self, request: web.Request) -> web.Response:
        text = await self.hass.async_add_executor_job(SCRIPT.read_text, "utf-8")
        return web.Response(text=text.replace("__HA_URL__", self.base_url), content_type="text/plain")

    async def async_handle_post(self, request: web.Request) -> web.Response:
        """Antwortet immer mit 200 und "OK …"/"ERROR …": BusyBox-wget zeigt bei Fehlercodes keinen Text."""
        try:
            body: Any = json.loads(await request.text())
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._reply("ERROR: invalid request")
        dev_id = str(body.get("id", "")).strip()
        secret = str(body.get("secret", "")).strip()
        if not dev_id.isdigit() or int(dev_id) == 0 or not _SECRET_RE.fullmatch(secret):
            return self._reply("ERROR: device ID or secret invalid")
        if body.get("dry_run"):
            return self._reply("OK, dry run: Home Assistant is reachable and the data is valid")
        current = str(self.entry.data.get(CONF_DEVICE_ID) or "")
        if current:
            if current == dev_id and self.entry.data.get(CONF_DEVICE_SECRET) == secret:
                return self._reply("OK, already set up")
            return self._reply("ERROR: Home Assistant is already set up with another fountain")
        _LOGGER.info("Brunnen %s meldet sich zur Einrichtung", dev_id)

        @callback
        def _store(_now: Any) -> None:
            # Erst nach der Antwort: Das Aktualisieren lädt die Integration neu (und damit diesen Server).
            self.hass.config_entries.async_update_entry(
                self.entry, unique_id=dev_id,
                data={**self.entry.data, CONF_DEVICE_ID: dev_id, CONF_DEVICE_SECRET: secret})

        async_call_later(self.hass, 1, _store)
        return self._reply("OK, fountain registered")

    @staticmethod
    def _reply(text: str) -> web.Response:
        return web.Response(text=text + "\n", content_type="text/plain")
