"""Zustand, Einstellungen und Befehlswarteschlange des Brunnens."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CAPACITY_INDATE,
    CAPACITY_NAMES,
    DEFAULT_PET_NAME,
    DOMAIN,
    DRINK_HISTORY_MAX,
    EVENT_BUS,
    EVENT_CLIP,
    EVENT_DRINK,
    ERROR_TEXT,
    EVENT_TYPES,
    MEDIA_SUBDIR,
    MEDIA_URL,
    ONLINE_TIMEOUT_S,
    SETTINGS_IMPORT_FILE,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Länger als das gilt ein Trinkvorgang ohne Ende-Meldung als abgebrochen.
DRINK_TIMEOUT_S = 15 * 60
# Tier erkannt bis so lange vor Trinkbeginn zählt noch zum selben Besuch.
VISIT_LEAD_S = 120
# Diese Felder schickt der Brunnen in Events mit (AES-Schlüssel der Clips!), wir geben sie nicht weiter.
PRIVATE_CONTENT_KEYS = frozenset({"aesKey", "img"})

# Menschen lösen „Trinken“ nur aus, wenn sie am Brunnen hantieren (Hand direkt vor der
# Kamera). Vorbeilaufen reicht nicht. Hantieren erkennen wir an Teilen, die entnommen oder
# eingesetzt werden, und an Aktionen, die jemand am Gerät startet. Trinkvorgänge in diesem
# Zeitfenster zählen nicht.
HANDLING_BEFORE_S = 120  # so lange vor Trinkbeginn
HANDLING_AFTER_S = 60    # so lange nach Trinkende
# Erst danach steht fest, ob ein Trinkvorgang zählt.
FINALIZE_DELAY_S = HANDLING_AFTER_S + 5
# Störungen, die Entnehmen/Einsetzen bedeuten: Wasserschale, Frisch-/Abwassertank, Heizschale
HANDLING_ERRORS = frozenset({"taryD", "tankCU", "tankDU", "ptcU"})
HANDLING_STATE_KEYS = ("stgInstall", "cwtInstall", "wtInstall", "heatInstall")
# work_start.reason: 0/1 = automatisch, ab 2 von Hand (App oder Taste am Gerät, laut Petkit-App)
MANUAL_REASON_MIN = 2
# Aktionen, die HA selbst geschickt hat, sind kein Hantieren am Gerät.
WORK_COMMANDS = frozenset({"start", "stop", "continue", "end"})
COMMAND_GRACE_S = 60
HANDLING_LOG_MAX = 50


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def event_time(event_id: str) -> int | None:
    """Startzeit steckt am Ende der Event-ID: '2_12345678_1791529445'."""
    tail = str(event_id).rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() and len(tail) >= 9 else None


def duration_text(seconds: int | None, german: bool = True) -> str:
    seconds = int(seconds or 0)
    minutes, sec = divmod(seconds, 60)
    if german:
        parts = []
        if minutes:
            parts.append(f"{minutes} Minute" + ("n" if minutes != 1 else ""))
        if sec or not minutes:
            parts.append(f"{sec} Sekunde" + ("n" if sec != 1 else ""))
        return " ".join(parts)
    parts = []
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if sec or not minutes:
        parts.append(f"{sec} second" + ("s" if sec != 1 else ""))
    return " ".join(parts)


class W7HDevice:
    """Hält alles, was Server und Entities gemeinsam brauchen."""

    def __init__(self, hass: HomeAssistant, entry_id: str, device_id: int, secret: str) -> None:
        self.hass = hass
        self.device_id = device_id
        self.secret = secret
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")

        self.identity: dict[str, Any] = {}
        self.settings: dict[str, Any] = {}
        self.capacity: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}
        # Nur bei uns verwaltet (nicht an das Gerät geschickt): Cube-Tausch, letzte Events
        self.local: dict[str, Any] = {"cube_durability": 30}
        # Trinkvorgänge (älteste zuerst) und Medien je Event-ID (Pfade relativ zum Medienordner)
        self.drinks: list[dict[str, Any]] = []
        self.media: dict[str, dict[str, Any]] = {}
        self.pet_name = DEFAULT_PET_NAME
        self.message_language = "de"
        self.only_known = False
        self.pets: Any = None  # W7HPets, setzt __init__
        self.last_seen: float | None = None

        self._queue: list[dict[str, Any]] = []
        self._queue_lock = threading.Lock()
        self._last_ts = 0
        self._listeners: list[Callable[[], None]] = []
        # Trinkvorgänge, die bei fehlender Wasserschale begonnen haben
        self._tray_out_drinks: set[str] = set()
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._last_command = 0.0  # letzte Arbeits-Aktion aus HA
        self._finalize_cancel: CALLBACK_TYPE | None = None

    # --- Persistenz ---------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        # Ältere Versionen haben nur das erste Zeichen gespeichert – solche Werte verwerfen.
        self.identity = {k: v for k, v in data.get("identity", {}).items()
                         if not (isinstance(v, str) and len(v) < 2)}
        self.settings = data.get("settings", {})
        self.capacity = data.get("capacity", [])
        self.local.update(data.get("local", {}))
        self.drinks = data.get("drinks", [])
        self.media = data.get("media", {})
        if not self.settings:
            imported = await self.hass.async_add_executor_job(self._read_import_file)
            if imported:
                self.capacity = imported.pop("capacity", [])
                self.settings = imported
                _LOGGER.info("Einstellungen aus %s übernommen (%d Werte)", SETTINGS_IMPORT_FILE, len(imported))
                self._schedule_save()

    def _read_import_file(self) -> dict[str, Any] | None:
        path = Path(self.hass.config.path(SETTINGS_IMPORT_FILE))
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _data(self) -> dict[str, Any]:
        return {"identity": self.identity, "settings": self.settings,
                "capacity": self.capacity, "local": self.local,
                "drinks": self.drinks, "media": self.media}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 2)

    @callback
    def async_start(self) -> None:
        """Nach dem Einrichten: offene Trinkvorgänge (z. B. vor einem Neustart) abschließen."""
        self._schedule_finalize()

    async def async_unload(self) -> None:
        if self._finalize_cancel:
            self._finalize_cancel()
            self._finalize_cancel = None
        await self._store.async_save(self._data())

    # --- Status vom Gerät ---------------------------------------------------

    @property
    def online(self) -> bool:
        return self.last_seen is not None and time.time() - self.last_seen < ONLINE_TIMEOUT_S

    @callback
    def async_seen(self) -> None:
        was_online = self.online
        self.last_seen = time.time()
        if not was_online:
            self._notify()

    @callback
    def async_update_identity(self, values: dict[str, Any]) -> None:
        changed = {k: v for k, v in values.items() if v and self.identity.get(k) != v}
        if changed:
            self.identity.update(changed)
            self._schedule_save()
            self._notify()

    @callback
    def async_update_state(self, state: dict[str, Any]) -> None:
        # Zur Bestimmung der noch unbelegten Codes (knapp/leer) jeden Wechsel festhalten.
        for key in ("cwtState", "wtState"):
            if key in state and state[key] != self.state.get(key):
                _LOGGER.info("%s: %s -> %s (Störungen: %s)", key, self.state.get(key), state[key],
                             ", ".join(self.active_errors) or "keine")
        for key in HANDLING_STATE_KEYS:
            old = self.state.get(key)
            if key in state and old is not None and state[key] != old:
                self._note_handling(f"{key} {old}->{state[key]}")
        self.state.update(state)
        self.async_seen()
        self._notify()

    @callback
    def async_handle_event(self, event_type: int, event_id: str, content: dict[str, Any] | None,
                           state: dict[str, Any] | None) -> None:
        if state:
            self.async_update_state(state)
        # Clip-Schlüssel und Vorschau-Pfad gehören nicht in Event-Attribute und Verlauf.
        content = {k: v for k, v in (content or {}).items() if k not in PRIVATE_CONTENT_KEYS}
        name = EVENT_TYPES.get(event_type, f"type_{event_type}")
        data = {"event": name, "event_type": event_type, "event_id": event_id, "content": content or {}}
        self.hass.bus.async_fire(EVENT_BUS, data)
        if name.startswith("type_"):
            _LOGGER.info("Unbekannter Event-Typ %s: %s", event_type, content)
        self.local[f"last_{name}"] = time.time()
        if name in ("error_start", "error_over") and content.get("err"):
            errors = [e for e in self.active_errors if e != content["err"]]
            if name == "error_start":
                errors.append(str(content["err"]))
            self.local["active_errors"] = errors
            if content["err"] in HANDLING_ERRORS:
                self._note_handling(f"{name} {content['err']}")
        if name == "work_start":
            reason = _int(content.get("reason"))
            if reason is not None and reason >= MANUAL_REASON_MIN and time.time() - self._last_command > COMMAND_GRACE_S:
                self._note_handling(f"work_start reason {reason}")
        if name == "pet_identified" and content:
            self.local["last_pet_id"] = content.get("pet_id")
            self._update_drink_pet(str(content.get("related_event", "")), content.get("pet_id"))
        if name == "drink_start":
            self.local["drink_active_since"] = time.time()
            if self._tray_missing(self.state):
                self._tray_out_drinks.add(event_id)
        if name == "drink_over" and content:
            self.local.pop("drink_active_since", None)
            drink = self._add_drink(event_id, content)
            if event_id in self._tray_out_drinks or self._tray_missing(self.state):
                drink["ignored"] = "tray_missing"
                _LOGGER.info("Trinkvorgang %s ignoriert: Wasserschale war nicht eingesetzt", event_id)
            else:
                # Zählt erst, wenn kurz danach niemand am Brunnen hantiert (siehe _finalize_drink).
                drink["pending"] = int(time.time()) + FINALIZE_DELAY_S
                self._schedule_finalize()
            self._tray_out_drinks.discard(event_id)
        self._schedule_save()
        self._notify()
        for listener in list(self._event_listeners):
            listener(name, data)

    # --- Trinkvorgänge und Clips ------------------------------------------

    @property
    def active_errors(self) -> list[str]:
        """Laufende Störungen aus den Störungs-Events (Start ohne Ende)."""
        return list(self.local.get("active_errors") or [])

    def error_texts(self) -> list[str]:
        """Alle aktiven Störungen (Zustand + Events) im Klartext."""
        idx = 0 if self.message_language == "de" else 1
        err = self.state.get("err") if isinstance(self.state.get("err"), dict) else {}
        codes = [c for c, on in err.items() if on]
        codes += [c for c in self.active_errors if c not in codes]
        return [ERROR_TEXT[c][idx] if c in ERROR_TEXT else c for c in codes]

    @staticmethod
    def _tray_missing(state: dict[str, Any]) -> bool:
        """Wasserschale draußen (z. B. beim Putzen): Dann schaut die Kamera auf den Boden und
        der Brunnen meldet „Trinken“, sobald irgendwo ein Tier oder ein Bein im Bild ist."""
        err = state.get("err") if isinstance(state.get("err"), dict) else {}
        return state.get("stgInstall") == 0 or bool(err.get("taryD"))

    @callback
    def _note_handling(self, why: str) -> None:
        """Jemand ist am Brunnen (Teil entnommen/eingesetzt, Taste gedrückt)."""
        now = int(time.time())
        log = [h for h in self.local.get("handling") or [] if h[0] > now - 86400]
        log.append([now, why])
        self.local["handling"] = log[-HANDLING_LOG_MAX:]
        _LOGGER.debug("Am Brunnen wird hantiert: %s", why)
        self._schedule_save()

    def _handling_near(self, drink: dict[str, Any]) -> str | None:
        start = drink.get("start") or 0
        end = drink.get("end") or start
        for ts, why in self.local.get("handling") or []:
            if start - HANDLING_BEFORE_S <= ts <= end + HANDLING_AFTER_S:
                return why
        return None

    @callback
    def _schedule_finalize(self) -> None:
        if self._finalize_cancel:
            self._finalize_cancel()
            self._finalize_cancel = None
        due = [d["pending"] for d in self.drinks if d.get("pending")]
        if due:
            delay = max(1.0, min(due) - time.time())
            self._finalize_cancel = async_call_later(self.hass, delay, self._async_finalize_due)

    @callback
    def _async_finalize_due(self, _now: Any = None) -> None:
        self._finalize_cancel = None
        now = time.time()
        for drink in self.drinks:
            if drink.get("pending") and drink["pending"] <= now:
                self._finalize_drink(drink)
        self._schedule_save()
        self._notify()
        self._schedule_finalize()

    def _finalize_drink(self, drink: dict[str, Any]) -> None:
        """Trinkvorgang zählt, wenn rund um ihn niemand am Brunnen hantiert hat."""
        drink.pop("pending", None)
        why = self._handling_near(drink)
        if why:
            drink["ignored"] = "handling"
            drink["handling"] = why
            _LOGGER.info("Trinkvorgang %s ignoriert: am Brunnen wurde hantiert (%s)", drink.get("id"), why)
            return
        self._fire_drink("drink", drink)
        media = self.media_for_drink(drink)
        if media.get("full") or media.get("video"):
            self._fire_drink("clip_ready", drink)

    @callback
    def async_ignore_drinks(self, event_ids: list[str], ignore: bool = True) -> int:
        """Trinkvorgänge von Hand aussortieren (oder wieder zulassen)."""
        count = 0
        for drink in self.drinks:
            if drink.get("id") in event_ids:
                if ignore:
                    drink["ignored"] = "manual"
                else:
                    drink.pop("ignored", None)
                    drink.pop("handling", None)
                count += 1
        if count:
            self._schedule_save()
            self._notify()
        return count

    @property
    def drinking(self) -> bool:
        since = self.local.get("drink_active_since")
        return bool(since) and time.time() - since < DRINK_TIMEOUT_S

    def _add_drink(self, event_id: str, content: dict[str, Any]) -> dict[str, Any]:
        start = _int(content.get("event_start")) or _int(content.get("start_time")) or event_time(event_id)
        end = _int(content.get("event_end")) or int(time.time())
        drink = {
            "id": event_id,
            "start": start or end,
            "end": end,
            "duration": _int(content.get("drink_time")) or 0,
            "pet_id": _int(content.get("pet_id")),
        }
        self.drinks = [d for d in self.drinks if d.get("id") != event_id or not event_id]
        self.drinks.append(drink)
        del self.drinks[:-DRINK_HISTORY_MAX]
        return drink

    def _update_drink_pet(self, related: str, pet_id: Any) -> None:
        """Tier zugeordnet (Event 24): gilt für den Trinkvorgang mit dieser ID oder,
        wenn sich die Zuordnung auf „Tier erkannt“ bezieht, für Trinkvorgänge desselben Besuchs."""
        pet_id = _int(pet_id)
        if not related or not pet_id:
            return
        seen = event_time(related)
        for drink in reversed(self.drinks[-20:]):
            if drink.get("id") == related:
                drink["pet_id"] = pet_id
            elif (not drink.get("pet_id") and seen and drink.get("start")
                  and drink["start"] - VISIT_LEAD_S <= seen <= (drink.get("end") or drink["start"])):
                drink["pet_id"] = pet_id

    def recognized_name(self, pet_id: int | None) -> str | None:
        """Name des Tiers, das der Brunnen am Gesicht erkannt hat."""
        if self.pets is None or not pet_id:
            return None
        return self.pets.name_for(pet_id)

    def _single_pet(self) -> str | None:
        """Gibt es nur ein Tier, gehört ihm jeder Trinkvorgang (Menschen filtert das Hantieren raus)."""
        pets = self.pets
        if pets is None:
            return self.pet_name
        names = list(dict.fromkeys([*pets.names, *(p["name"] for p in pets.pets)]))
        return names[0] if len(names) == 1 else None

    def _known_name(self, pet_id: int | None) -> str | None:
        return self.recognized_name(pet_id) or self._single_pet()

    def is_recognized(self, drink: dict[str, Any]) -> bool:
        return self.recognized_name(drink.get("pet_id")) is not None

    def is_known(self, drink: dict[str, Any]) -> bool:
        return self._known_name(drink.get("pet_id")) is not None

    def _unknown(self) -> str:
        return "Unbekannt" if self.message_language == "de" else "Unknown"

    def pet_label(self, pet_id: int | None) -> str:
        return self._known_name(pet_id) or self._unknown()

    def recognized_label(self, pet_id: int | None) -> str:
        return self.recognized_name(pet_id) or self._unknown()

    def drink_message(self, drink: dict[str, Any]) -> str:
        german = self.message_language == "de"
        dur = duration_text(drink.get("duration"), german)
        pet = self._known_name(drink.get("pet_id"))
        if pet is None:
            return f"Unbekanntes Tier hat {dur} getrunken" if german else f"Unknown pet drank for {dur}"
        return f"{pet} hat {dur} getrunken" if german else f"{pet} drank for {dur}"

    def visible_drinks(self) -> list[dict[str, Any]]:
        """Trinkvorgänge für Sensoren und Karte: ohne aussortierte und noch offene,
        auf Wunsch nur die per Gesicht erkannten."""
        return [d for d in self.drinks
                if not d.get("ignored") and not d.get("pending")
                and (not self.only_known or self.is_recognized(d))]

    def media_url(self, rel: str | None, version: int | None = None) -> str | None:
        if not rel:
            return None
        return f"{MEDIA_URL}/{rel}?v={version}" if version and version > 1 else f"{MEDIA_URL}/{rel}"

    def media_for_drink(self, drink: dict[str, Any]) -> dict[str, Any]:
        """Medien zum Trinkvorgang: gleiche Event-ID, sonst ein Clip aus demselben Zeitraum."""
        media = self.media.get(drink.get("id", ""))
        if media:
            return media
        start, end = drink.get("start") or 0, drink.get("end") or 0
        for entry in self.media.values():
            if (entry.get("full") or entry.get("video")) and start - 120 <= (entry.get("start") or 0) <= end + 30:
                return entry
        return {}

    def drink_media_rels(self) -> set[str]:
        """Dateien gezählter (und noch offener) Trinkvorgänge: Die bleiben beim Aufräumen länger."""
        rels: set[str] = set()
        for drink in self.drinks:
            if drink.get("ignored"):
                continue
            entry = self.media_for_drink(drink)
            rels |= {entry[k] for k in ("thumb", "video", "full") if entry.get(k)}
        return rels

    def drink_view(self, drink: dict[str, Any]) -> dict[str, Any]:
        """Kompakte Darstellung für Attribute, Events und die Dashboard-Karte."""
        media = self.media_for_drink(drink)
        clip_kind = "full" if media.get("full") else "video"
        clip = media.get(clip_kind)
        return {
            "id": drink.get("id"),
            "start": dt_util.utc_from_timestamp(drink["start"]).isoformat() if drink.get("start") else None,
            "duration": drink.get("duration"),
            "pet": self.pet_label(drink.get("pet_id")),
            "pet_id": drink.get("pet_id"),
            "known": self.is_known(drink),
            "recognized": self.is_recognized(drink),
            "message": self.drink_message(drink),
            "clip": self.media_url(clip, media.get(f"{clip_kind}_v")),
            "thumb": self.media_url(media.get("thumb"), media.get("thumb_v")),
        }

    def drinks_on(self, day: Any) -> list[dict[str, Any]]:
        """Trinkvorgänge an einem lokalen Kalendertag."""
        return [d for d in self.visible_drinks()
                if d.get("start") and dt_util.as_local(dt_util.utc_from_timestamp(d["start"])).date() == day]

    @property
    def last_drink(self) -> dict[str, Any] | None:
        drinks = self.visible_drinks()
        return drinks[-1] if drinks else None

    @callback
    def async_media_ready(self, event_id: str, kind: str, rel: str, info: dict[str, Any]) -> None:
        """Eine Datei (thumb/video/full) zu einem Event liegt fertig im Medienordner."""
        entry = self.media.setdefault(event_id, {"start": event_time(event_id) or int(time.time())})
        previous = entry.get(kind)
        entry[kind] = rel
        # Neu gebaut (z. B. längeres Event-Video): neue Version, damit Browser nicht die alte zeigen.
        entry[f"{kind}_v"] = entry.get(f"{kind}_v", 0) + 1 if previous == rel else 1
        for flag in ("drinkEvent", "petEvent", "flushEvent", "addWaterEvent"):
            if info.get(flag):
                entry[flag] = 1
        self._schedule_save()
        self._notify()
        if kind == "thumb" or previous == rel:
            return
        drink = next((d for d in reversed(self.drinks[-50:])
                      if d.get("id") == event_id or self.media_for_drink(d) is entry), None)
        payload = {"event_id": event_id, "kind": kind, "url": self.media_url(rel),
                   # für Benachrichtigungen der Companion-App (lokale Medienquelle)
                   "media_path": f"/media/local/{MEDIA_SUBDIR}/{rel}"}
        if drink:
            payload.update(self.drink_view(drink))
        self.hass.bus.async_fire(EVENT_CLIP, payload)
        if drink:
            self._fire_drink("clip_ready", drink)

    @callback
    def async_media_removed(self, rels: set[str]) -> None:
        """Aufräumen hat Dateien gelöscht: Verweise entfernen."""
        for event_id in list(self.media):
            entry = self.media[event_id]
            for kind in ("thumb", "video", "full"):
                if entry.get(kind) in rels:
                    entry.pop(kind)
                    entry.pop(f"{kind}_v", None)
            if not any(entry.get(k) for k in ("thumb", "video", "full")):
                self.media.pop(event_id)
        self._schedule_save()
        self._notify()

    def _fire_drink(self, kind: str, drink: dict[str, Any]) -> None:
        if drink.get("ignored") or drink.get("pending") or (self.only_known and not self.is_recognized(drink)):
            return
        view = self.drink_view(drink)
        if kind == "drink":
            self.hass.bus.async_fire(EVENT_DRINK, view)
        for listener in list(self._event_listeners):
            listener(kind, {"event": kind, "event_id": drink.get("id"), "content": view})

    # --- Aufnahme-Kontingent --------------------------------------------------

    def capacity_for_device(self) -> list[dict[str, Any]]:
        """Kontingent für Clips: lokal unbegrenzt (Petkit gibt das nur mit Abo)."""
        known = {c.get("name"): c for c in self.capacity if isinstance(c, dict)}
        out = []
        for name in CAPACITY_NAMES:
            entry = dict(known.get(name) or {"name": name, "workTime": int(time.time())})
            entry["indate"] = CAPACITY_INDATE
            out.append(entry)
        return out

    @callback
    def async_ensure_capacity(self) -> None:
        """Kontingent einmalig live nachschieben (sonst erst beim nächsten Neustart des Brunnens)."""
        wanted = self.capacity_for_device()
        if self.capacity == wanted:
            return
        self.capacity = wanted
        self._schedule_save()
        self._enqueue({"msgType": 1, "type": "property_set", "payload": {"capacity": wanted}})
        _LOGGER.info("Aufnahme-Kontingent an den Brunnen geschickt")

    # --- Befehle an das Gerät -----------------------------------------------

    async def async_set_settings(self, values: dict[str, Any]) -> None:
        """Einstellungen setzen: merken (für dev_device_info) und per Heartbeat schicken."""
        self.settings.update(values)
        self._schedule_save()
        self._enqueue({"msgType": 1, "type": "property_set", "payload": values})
        self._notify()

    @callback
    def async_set_local(self, values: dict[str, Any]) -> None:
        self.local.update(values)
        self._schedule_save()
        self._notify()

    async def async_invoke(self, service: str, payload: dict[str, Any] | None = None) -> None:
        if service in WORK_COMMANDS:
            self._last_command = time.time()
        self._enqueue({"msgType": 2, "type": service, "payload": payload or {}})

    def _enqueue(self, msg: dict[str, Any]) -> None:
        with self._queue_lock:
            self._queue.append(msg)
        _LOGGER.debug("Befehl eingereiht: %s", msg)

    def pop_heartbeat_items(self) -> list[dict[str, Any]]:
        """Heartbeat-Einträge für alle wartenden Befehle (leer = keiner wartet)."""
        items = []
        with self._queue_lock:
            while self._queue:
                msg = self._queue.pop(0)
                # timestamp muss streng steigen, sonst gilt die Nachricht als verbraucht.
                ts = self._last_ts = max(int(time.time()), self._last_ts + 1)
                msg["timestamp"] = ts
                items.append({
                    "content": json.dumps(msg, separators=(",", ":")),
                    "time": int(time.time() * 1000),
                    "timestamp": ts,
                })
        return items

    # --- Listener -----------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add_event_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
