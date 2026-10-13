"""Tiererkennung: Referenzfotos je Tier für den Brunnen (`dev_discern_pic`).

So arbeitet der Brunnen (FW 456, `ctrl` + `/alg/libalgo.so`):
- Er holt `dev_discern_pic` beim Start und nach dem Dienst `discern`
  (`{"discern_get": 1}`). Erlaubt sind höchstens 20 Tiere mit je 6 Fotos.
- Jedes Foto lädt er per wget. Die URL enthält deshalb nur einfache Zeichen
  (kein `&`, kein `?`).
- `petkit_from_url_detect_feature` liest das Bild mit `cv::imread` und nimmt es
  nur bei **genau 224×224 Pixeln**. Eine eigene Gesichtssuche gibt es dabei
  nicht, das Bild geht direkt in das Merkmalsmodell. Unter einem Qualitätswert
  von 0,5 wird es verworfen.

Deshalb schneiden wir die Fotos mittig quadratisch zu und skalieren sie auf
224×224. Gute Fotos zeigen das Gesicht formatfüllend und frontal.

Ablage: <media>/petkit_w7h/pets/<Name>/*.jpg. Ein Ordner pro Tier, der
Ordnername ist der Name des Tiers.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import secrets
from typing import Any
import zlib

from aiohttp import web
from PIL import Image, ImageOps, UnidentifiedImageError

from homeassistant.core import HomeAssistant

from .device import W7HDevice
from .media import media_root

_LOGGER = logging.getLogger(__name__)

PET_SIZE = 224
MAX_PETS = 20
MAX_PHOTOS = 6
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
COLORS = ("#FFB300", "#42A5F5", "#66BB6A", "#EF5350", "#AB47BC", "#26C6DA", "#FF7043", "#8D6E63")
_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def split_names(value: str | None) -> list[str]:
    """„Luna, Felix“ → ["Luna", "Felix"] (ohne Zeichen, die im Ordnernamen stören)."""
    names = []
    for part in (value or "").split(","):
        name = _NAME_RE.sub("", part).strip().strip(".")
        if name and name not in names:
            names.append(name)
    return names


class W7HPets:
    """Tiere mit Referenzfotos, wie sie der Brunnen für die Erkennung bekommt."""

    def __init__(self, hass: HomeAssistant, device: W7HDevice, base_url: str, names: list[str]) -> None:
        self.hass = hass
        self.device = device
        self.base_url = base_url.rstrip("/")
        self.names = names
        self.root = media_root(hass) / "pets"
        self.cache = media_root(hass) / ".pets"
        # [{"id", "name", "color", "photos": [face_id, …]}]
        self.pets: list[dict[str, Any]] = []
        self._bad: set[tuple[str, int]] = set()  # unlesbare Fotos nur einmal melden

    # --- Zuordnung Name ↔ ID (bleibt dauerhaft gleich) -------------------------

    def _pet_ids(self) -> dict[str, int]:
        return dict(self.device.local.get("pet_ids") or {})

    def _assign_ids(self, names: list[str]) -> dict[str, int]:
        current = self._pet_ids()
        # Gelöschte Tier-Ordner fallen raus, neue bekommen die nächste freie ID.
        ids = {n: i for n, i in current.items() if n in names}
        changed = ids != current
        for name in names:
            if name not in ids:
                ids[name] = max(ids.values(), default=0) + 1
                changed = True
        if changed:
            self.device.async_set_local({"pet_ids": ids})
        return ids

    @property
    def token(self) -> str:
        value = self.device.local.get("pet_token")
        if not value:
            value = secrets.token_hex(12)
            self.device.async_set_local({"pet_token": value})
        return value

    @property
    def recognition_active(self) -> bool:
        return any(p["photos"] for p in self.pets)

    def name_for(self, pet_id: int | None) -> str | None:
        for pet in self.pets:
            if pet["id"] == pet_id:
                return pet["name"]
        for name, pid in self._pet_ids().items():
            if pid == pet_id:
                return name
        return None

    # --- Fotos einlesen ---------------------------------------------------------

    async def async_scan(self) -> bool:
        """Ordner einlesen, Fotos aufbereiten. True = für den Brunnen hat sich etwas geändert."""
        folders = await self.hass.async_add_executor_job(self._ensure_folders)
        ids = self._assign_ids(folders)
        pets = await self.hass.async_add_executor_job(self._scan, folders, ids)
        changed = [(p["id"], p["photos"]) for p in pets] != [(p["id"], p["photos"]) for p in self.pets]
        self.pets = pets
        signature = repr([(p["id"], p["name"], p["photos"]) for p in pets])
        if signature != self.device.local.get("discern_signature"):
            self.device.async_set_local({"discern_signature": signature})
            return True
        return changed

    def _ensure_folders(self) -> list[str]:
        """Ordner für die eingestellten Namen anlegen, alle vorhandenen Tier-Ordner liefern."""
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.names:
            (self.root / name).mkdir(exist_ok=True)
        folders = sorted(d.name for d in self.root.iterdir() if d.is_dir() and not d.name.startswith("."))
        # Eingestellte Namen zuerst, dann weitere Ordner (z. B. von Hand angelegt)
        ordered = [n for n in self.names if n in folders] + [n for n in folders if n not in self.names]
        if len(ordered) > MAX_PETS:
            _LOGGER.warning("Mehr als %d Tier-Ordner, der Brunnen nimmt nur die ersten", MAX_PETS)
        return ordered[:MAX_PETS]

    def _scan(self, folders: list[str], ids: dict[str, int]) -> list[dict[str, Any]]:
        pets = []
        for index, name in enumerate(folders):
            pet_id = ids[name]
            files = sorted(f for f in (self.root / name).iterdir()
                           if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES and not f.name.startswith("."))
            if len(files) > MAX_PHOTOS:
                _LOGGER.warning("%s: %d Fotos, der Brunnen nimmt nur %d (die ersten nach Dateiname)",
                                name, len(files), MAX_PHOTOS)
            photos = []
            for src in files[:MAX_PHOTOS]:
                face_id = self._prepare(pet_id, src)
                if face_id:
                    photos.append(face_id)
            pets.append({"id": pet_id, "name": name, "color": COLORS[index % len(COLORS)], "photos": photos})
        self._prune_cache(pets)
        return pets

    def _prepare(self, pet_id: int, src: Path) -> int | None:
        """Foto mittig quadratisch zuschneiden und als 224×224-JPEG ablegen."""
        data = src.read_bytes()
        if (str(src), len(data)) in self._bad:
            return None
        # Der Brunnen liest die ID als 32-Bit-int (cJSON valueint): größere Werte landen
        # alle auf 2147483647 und gelten dann als dasselbe Foto. Deshalb nur 31 Bit.
        face_id = (zlib.crc32(data) & 0x7FFFFFFF) or 1
        out = self.cache / str(pet_id) / f"{face_id}.jpg"
        if out.exists():
            return face_id
        try:
            with Image.open(src) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img = ImageOps.fit(img, (PET_SIZE, PET_SIZE), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_name(f".{out.name}.part")
                img.save(tmp, "JPEG", quality=92)
                tmp.replace(out)
        except (UnidentifiedImageError, OSError) as err:
            self._bad.add((str(src), len(data)))
            _LOGGER.warning("Foto %s lässt sich nicht lesen (HEIC bitte als JPEG speichern): %s", src.name, err)
            return None
        return face_id

    def _prune_cache(self, pets: list[dict[str, Any]]) -> None:
        keep = {f"{p['id']}/{fid}.jpg" for p in pets for fid in p["photos"]}
        if not self.cache.is_dir():
            return
        for f in self.cache.glob("*/*.jpg"):
            if f"{f.parent.name}/{f.name}" not in keep:
                f.unlink(missing_ok=True)

    # --- Für den Brunnen ------------------------------------------------------------

    def discern_list(self) -> dict[str, Any]:
        """Antwort auf dev_discern_pic."""
        out = []
        for pet in self.pets:
            if not pet["photos"]:
                continue
            out.append({
                "id": pet["id"],
                "color": pet["color"],
                "petName": pet["name"][:63],
                "discern": [{"id": fid, "url": f"{self.base_url}/pet/{self.token}/{pet['id']}/{fid}.jpg"}
                            for fid in pet["photos"]],
            })
        return {"list": out}

    async def async_handle_get(self, request: web.Request) -> web.StreamResponse:
        if not secrets.compare_digest(request.match_info.get("token", ""), self.token):
            return web.Response(status=404)
        pet_id = request.match_info.get("pet", "")
        name = request.match_info.get("file", "")
        if not pet_id.isdigit() or not re.fullmatch(r"\d+\.jpg", name):
            return web.Response(status=404)
        path = self.cache / pet_id / name
        if not await self.hass.async_add_executor_job(path.is_file):
            return web.Response(status=404)
        _LOGGER.debug("Brunnen holt Tierfoto %s/%s", pet_id, name)
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg"})
