"""Einrichtung über die HA-Oberfläche."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import (
    CONF_CLIPS,
    CONF_DEVICE_ID,
    CONF_DEVICE_SECRET,
    CONF_MAX_STORAGE_GB,
    CONF_MESSAGE_LANGUAGE,
    CONF_ONLY_KNOWN,
    CONF_PET_NAME,
    CONF_PORT,
    CONF_PUBLIC_HOST,
    CONF_RETENTION_DAYS,
    CONF_RETENTION_OTHER_DAYS,
    DEFAULT_MAX_STORAGE_GB,
    DEFAULT_PET_NAME,
    DEFAULT_PET_NAME_EN,
    DEFAULT_PORT,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RETENTION_OTHER_DAYS,
    DOMAIN,
)
from .onboarding import setup_command

DEVICE_FILE = "petkit_w7h_device.json"


def default_pet_name(language: str) -> str:
    return DEFAULT_PET_NAME if language == "de" else DEFAULT_PET_NAME_EN


def default_message_language(hass: HomeAssistant) -> str:
    if hass.config.language.startswith("de") or hass.config.country in ("DE", "AT", "CH", "LI"):
        return "de"
    return "en"


class W7HConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> W7HOptionsFlow:
        return W7HOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Adresse von HA eintragen. Geräte-ID und Secret sind optional: Ohne sie holt sich die
        Integration beides per setup.sh vom Brunnen (onboarding.py)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            dev_id = str(user_input.get(CONF_DEVICE_ID) or "").strip()
            secret = str(user_input.get(CONF_DEVICE_SECRET) or "").strip()
            if dev_id and not dev_id.isdigit():
                errors[CONF_DEVICE_ID] = "invalid_id"
            elif bool(dev_id) != bool(secret):
                errors["base"] = "id_and_secret"
            if not errors:
                data = {**user_input, CONF_DEVICE_ID: dev_id, CONF_DEVICE_SECRET: secret}
                if dev_id:
                    await self.async_set_unique_id(dev_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title="Eversweet Ultra", data=data)
                base = f"http://{user_input[CONF_PUBLIC_HOST]}:{user_input[CONF_PORT]}"
                return self.async_create_entry(
                    title="Eversweet Ultra", data=data, description="setup_command",
                    description_placeholders={"command": setup_command(base)})

        host = await network.async_get_source_ip(self.hass)
        known = await self.hass.async_add_executor_job(self._read_device_file)
        schema = vol.Schema({
            vol.Required(CONF_PUBLIC_HOST, default=host): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_DEVICE_ID, description={"suggested_value": str(known.get("id") or "")}): str,
            vol.Optional(CONF_DEVICE_SECRET, description={"suggested_value": known.get("secret") or ""}): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    def _read_device_file(self) -> dict[str, Any]:
        """Optional: Vorbelegung aus /config/petkit_w7h_device.json ({"id": …, "secret": …})."""
        path = Path(self.hass.config.path(DEVICE_FILE))
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            return {}


class W7HOptionsFlow(OptionsFlow):
    """Namen der Tiere, Sprache, Filter, Clips an/aus und wie lange sie bleiben."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_PET_NAME, default=opts.get(CONF_PET_NAME, default_pet_name(
                opts.get(CONF_MESSAGE_LANGUAGE) or default_message_language(self.hass)))): str,
            vol.Required(CONF_MESSAGE_LANGUAGE,
                         default=opts.get(CONF_MESSAGE_LANGUAGE, default_message_language(self.hass))):
                SelectSelector(SelectSelectorConfig(options=["de", "en"], mode=SelectSelectorMode.DROPDOWN,
                                                    translation_key=CONF_MESSAGE_LANGUAGE)),
            vol.Required(CONF_ONLY_KNOWN, default=opts.get(CONF_ONLY_KNOWN, False)): bool,
            vol.Required(CONF_CLIPS, default=opts.get(CONF_CLIPS, True)): bool,
            vol.Required(CONF_RETENTION_DAYS, default=opts.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            vol.Required(CONF_RETENTION_OTHER_DAYS,
                         default=opts.get(CONF_RETENTION_OTHER_DAYS, DEFAULT_RETENTION_OTHER_DAYS)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            vol.Required(CONF_MAX_STORAGE_GB, default=opts.get(CONF_MAX_STORAGE_GB, DEFAULT_MAX_STORAGE_GB)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
