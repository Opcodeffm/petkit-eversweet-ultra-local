"""Clips: nachgebauter Objektspeicher für den Brunnen, Entschlüsseln und Umpacken.

Ablauf beim Gerät (Prozess `cloud`):
1. `dev_oss_sts_info_new_v2` liefert Upload-Ziel und AES-Schlüssel (OCI-Format).
2. Das Gerät verschlüsselt jede Datei mit AES-128-CBC (Schlüssel = die 16 Zeichen
   aus `primaryAesKeyStr`, IV meldet es mit) und lädt sie per HTTP-PUT nach
   `<primaryParUrl><pathPrefix>/<fileId>` hoch.
3. Danach meldet es `dev_upload_file_info_v2` mit `fileInfos` (eventId, moduleType,
   aesIv, Zeiten). Erst dann kennen wir den IV und können entschlüsseln.

Videos kommen als MPEG-TS. Wir packen sie mit ffmpeg zu MP4 um (H.264 bleibt
unverändert, HEVC wird für Browser umgerechnet). Ablage unter
<media>/petkit_w7h/clips/<Datum>/, damit sie auch in "Medien" von HA auftauchen.

Das Verfahren (OCI-förmige Antwort, Entschlüsseln mit gemeldetem IV) stammt aus
localkit (github.com/dwyschka/localkit), hier eigenständig nachgebaut.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, timedelta
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any

from aiohttp import web
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import MEDIA_SUBDIR
from .device import W7HDevice, event_time

_LOGGER = logging.getLogger(__name__)

NAMESPACE = "local"
BUCKET = "w7h"
CYCLE_TYPES = ("eventImage", "highLight", "dynamicVideo", "fullVideo")
MAX_UPLOAD = 64 * 1024 * 1024
# Lange Event-Videos kommen in Stücken (5–20 s), teils mit über einer Minute Pause.
# So lange nach dem letzten Stück bauen wir das MP4 (jedes weitere Stück: neu bauen) …
FULL_SETTLE_S = 45
# … und so lange heben wir die gesammelten Stücke für Nachzügler auf.
FULL_KEEP_S = 15 * 60
FULL_MODULE = "CLOUD_STORAGE"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    return _SAFE_RE.sub("_", name)[:128].lstrip(".")


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def media_root(hass: HomeAssistant) -> Path:
    base = hass.config.media_dirs.get("local") or hass.config.path("media")
    return Path(base) / MEDIA_SUBDIR


def _iv_bytes(value: Any) -> bytes | None:
    text = str(value or "")
    if text.lower().startswith("0x"):
        text = text[2:]
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            return None
    if len(text) == 16:
        return text.encode()
    return None


def _kind(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:1] == b"\x47" and (len(data) < 189 or data[188:189] == b"\x47"):
        return "ts"
    return None


def decrypt(data: bytes, key: str, iv: bytes) -> bytes:
    """AES-128-CBC über die ganze Datei, PKCS#7-Padding am Ende."""
    full = len(data) - len(data) % 16
    dec = Cipher(algorithms.AES(key.encode()), modes.CBC(iv)).decryptor()
    plain = dec.update(data[:full]) + dec.finalize() + data[full:]
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain


class W7HMedia:
    """Nimmt Uploads des Brunnens an und macht daraus abspielbare Clips."""

    def __init__(self, hass: HomeAssistant, device: W7HDevice, base_url: str) -> None:
        self.hass = hass
        self.device = device
        self.base_url = base_url.rstrip("/")
        self.root = media_root(hass)
        self.incoming = self.root / ".incoming"
        self.work = self.root / ".work"
        self.enabled = True
        self._lock = asyncio.Lock()
        self._full_timers: dict[str, Callable[[], None]] = {}
        self._drop_timers: dict[str, Callable[[], None]] = {}
        self._seen_modules: set[str] = set()

    # --- Zugangsdaten für das Gerät -------------------------------------------

    def _local_secret(self, key: str, nbytes: int) -> str:
        value = self.device.local.get(key)
        if not value:
            value = secrets.token_hex(nbytes)
            self.device.async_set_local({key: value})
        return value

    @property
    def aes_key(self) -> str:
        # 16 Zeichen; das Gerät nimmt sie direkt als 128-Bit-Schlüssel.
        return self._local_secret("aes_key", 8)

    @property
    def par_token(self) -> str:
        return self._local_secret("par_token", 16)

    @property
    def path_prefix(self) -> str:
        return f"w7h/{self.device.device_id}"  # das Gerät kopiert höchstens 16 Zeichen

    def capability(self) -> dict[str, Any]:
        """Antwort auf dev_oss_sts_info_new_v2 (leer = keine Uploads)."""
        if not self.enabled:
            return {}
        now = int(time.time())
        domain = f"{self.base_url}/oci/n/{NAMESPACE}/b/{BUCKET}/o/"
        par = f"{self.base_url}/oci/p/{self.par_token}/n/{NAMESPACE}/b/{BUCKET}/o/"
        key = self.aes_key
        common = {
            "AesKeyStr": key,
            "AesKeyUri": f"{domain}AESKEY/{self.device.device_id}.txt",
            "BucketName": BUCKET,
            "Domain": domain,
            "ParUrl": par,
            "ParExpiration": (now + 12 * 3600) * 1000,
            # Das Gerät verlangt OSS-Zugangsdaten, wir prüfen sie nicht.
            "AccessKeyId": "local",
            "AccessKeySecret": "local",
            "SecurityToken": "local",
        }
        entries = []
        for cycle_type in CYCLE_TYPES:
            entry: dict[str, Any] = {
                "deviceId": self.device.device_id,
                "cycleType": cycle_type,
                "cycle": 1,
                "cycleExpiration": now + 30 * 86400,
                "pathPrefix": self.path_prefix,
                "isHD": 0,
            }
            for side in ("primary", "standby"):
                entry.update({f"{side}{k}": v for k, v in common.items()})
            entries.append(entry)
        return {"type": "oci", "capability": entries}

    # --- Objektspeicher ---------------------------------------------------------

    async def async_handle_put(self, request: web.Request) -> web.Response:
        token = request.match_info.get("token", "")
        if not self.enabled or not secrets.compare_digest(token, self.par_token):
            return web.Response(status=403)
        name = _safe(request.match_info.get("obj", "").rsplit("/", 1)[-1])
        if not name:
            return web.Response(status=400)
        buf = bytearray()
        async for chunk in request.content.iter_chunked(256 * 1024):
            buf.extend(chunk)
            if len(buf) > MAX_UPLOAD:
                return web.Response(status=413)
        await self.hass.async_add_executor_job(self._write_incoming, name, bytes(buf))
        _LOGGER.debug("Upload %s (%d Bytes)", name, len(buf))
        return web.Response(status=200, headers={"ETag": f'"{secrets.token_hex(8)}"'})

    def _write_incoming(self, name: str, data: bytes) -> None:
        self.incoming.mkdir(parents=True, exist_ok=True)
        tmp = self.incoming / f".{name}.part"
        tmp.write_bytes(data)
        tmp.replace(self.incoming / name)

    async def async_handle_get(self, request: web.Request) -> web.Response:
        obj = request.match_info.get("obj", "")
        if self.enabled and obj.startswith("AESKEY/"):
            return web.Response(text=self.aes_key)
        return web.Response(status=404)

    # --- Upload-Meldungen ---------------------------------------------------------

    @callback
    def async_file_infos(self, infos: list[dict[str, Any]]) -> None:
        if infos:
            self.hass.async_create_background_task(self._process_all(infos), "petkit_w7h_media")

    async def _process_all(self, infos: list[dict[str, Any]]) -> None:
        async with self._lock:
            for info in infos:
                if not isinstance(info, dict):
                    continue
                try:
                    await self._process(info)
                except Exception:  # noqa: BLE001 - eine kaputte Datei darf den Rest nicht aufhalten
                    _LOGGER.exception("Clip-Datei %s ließ sich nicht verarbeiten", info.get("fileId"))

    async def _process(self, info: dict[str, Any]) -> None:
        name = _safe(str(info.get("fileId") or "").rsplit("/", 1)[-1])
        if not name:
            return
        event_id = str(info.get("eventId") or name)
        module = str(info.get("moduleType") or "")
        if module not in self._seen_modules:
            self._seen_modules.add(module)
            _LOGGER.info("Erster Upload vom Typ %s: %s", module or "?",
                         {k: v for k, v in info.items() if k not in ("fileUrl", "aesIv")})
        data = await self.hass.async_add_executor_job(self._take_incoming, name, info, self.aes_key)
        if data is None:
            _LOGGER.warning("Gemeldete Datei %s fehlt oder ist unlesbar", name)
            return
        kind = _kind(data)
        start = event_time(event_id) or _int(info.get("startTime")) or int(time.time())
        if start > 10**11:  # Millisekunden
            start //= 1000
        local = dt_util.as_local(dt_util.utc_from_timestamp(start))
        rel_dir = f"clips/{local:%Y-%m-%d}"
        stem = f"{local:%H-%M-%S}_{_safe(event_id)}"

        if kind == "jpg":
            rel = f"{rel_dir}/{stem}.jpg"
            await self.hass.async_add_executor_job(self._write_file, rel, data)
            self.device.async_media_ready(event_id, "thumb", rel, info)
        elif kind == "ts" and module == FULL_MODULE:
            ts = self.work / f"{stem}_full.ts"
            await self.hass.async_add_executor_job(self._append, ts, data)
            self._schedule_full(event_id, ts, f"{rel_dir}/{stem}_full.mp4", info)
        elif kind == "ts":
            # Kurze Vorschau-Clips: nur umpacken, nie neu kodieren (das lange Video kommt ohnehin).
            suffix = _safe(module.lower()) or "video"
            ts = self.work / f"{stem}_{suffix}.ts"
            await self.hass.async_add_executor_job(self._write_path, ts, data)
            rel = f"{rel_dir}/{stem}_{suffix}.mp4"
            if await self._to_mp4(ts, self.root / rel, transcode=False):
                await self._ensure_thumb(event_id, rel, rel_dir, stem)
                self.device.async_media_ready(event_id, "video", rel, info)
                await self.hass.async_add_executor_job(ts.unlink, True)
            else:
                await self.hass.async_add_executor_job(self._keep_failed, ts)
        else:
            rel = f".work/{stem}_{_safe(module.lower()) or 'unknown'}.bin"
            await self.hass.async_add_executor_job(self._write_file, rel, data)
            _LOGGER.warning("Unbekanntes Dateiformat (%s, Anfang %s), abgelegt als %s",
                            module, data[:8].hex(), rel)

    def _take_incoming(self, name: str, info: dict[str, Any], key: str) -> bytes | None:
        src = self.incoming / name
        try:
            raw = src.read_bytes()
        except OSError:
            return None
        src.unlink(missing_ok=True)
        if _kind(raw):  # unverschlüsselt angekommen
            return raw
        iv = _iv_bytes(info.get("aesIv")) or b"a" * 16
        return decrypt(raw, key, iv)

    def _write_file(self, rel: str, data: bytes) -> None:
        self._write_path(self.root / rel, data)

    @staticmethod
    def _write_path(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.part")
        tmp.write_bytes(data)
        tmp.replace(path)

    @staticmethod
    def _append(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(data)

    # --- Lange Event-Videos ---------------------------------------------------------

    def _schedule_full(self, event_id: str, ts: Path, rel: str, info: dict[str, Any]) -> None:
        for timers in (self._full_timers, self._drop_timers):
            if cancel := timers.pop(event_id, None):
                cancel()

        @callback
        def _build(_now: Any) -> None:
            self._full_timers.pop(event_id, None)
            self.hass.async_create_background_task(self._build_full(event_id, ts, rel, info),
                                                   "petkit_w7h_full_clip")

        @callback
        def _drop(_now: Any) -> None:
            self._drop_timers.pop(event_id, None)
            self.hass.async_create_background_task(self._drop_full(ts), "petkit_w7h_full_clip_done")

        self._full_timers[event_id] = async_call_later(self.hass, FULL_SETTLE_S, _build)
        self._drop_timers[event_id] = async_call_later(self.hass, FULL_KEEP_S, _drop)

    async def _build_full(self, event_id: str, ts: Path, rel: str, info: dict[str, Any]) -> None:
        """MP4 aus allen bisher angekommenen Stücken bauen (Stücke bleiben für Nachzügler liegen)."""
        async with self._lock:
            if not await self.hass.async_add_executor_job(ts.exists):
                return
            if await self._to_mp4(ts, self.root / rel):
                rel_dir, name = rel.rsplit("/", 1)
                await self._ensure_thumb(event_id, rel, rel_dir, name.removesuffix("_full.mp4"))
                self.device.async_media_ready(event_id, "full", rel, info)

    async def _drop_full(self, ts: Path) -> None:
        async with self._lock:
            await self.hass.async_add_executor_job(ts.unlink, True)

    # --- ffmpeg ---------------------------------------------------------------------

    async def _ffmpeg(self, *args: Any, timeout: float = 300, loglevel: str = "error") -> tuple[int, str]:
        binary = get_ffmpeg_manager(self.hass).binary
        proc = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-nostdin", "-loglevel", loglevel, *map(str, args),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return -1, "timeout"
        return proc.returncode or 0, err.decode("utf-8", "replace")

    async def _to_mp4(self, src: Path, dst: Path, transcode: bool = True) -> bool:
        """TS → MP4. H.264 wird nur umgepackt; anderes (z. B. HEVC) bei transcode=True umgerechnet."""
        await self.hass.async_add_executor_job(lambda: dst.parent.mkdir(parents=True, exist_ok=True))
        tmp = dst.with_name(f".{dst.stem}.tmp.mp4")
        _, probe = await self._ffmpeg("-i", src, loglevel="info")
        is_h264 = "Video: h264" in probe
        x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        audio = ["-c:a", "aac", "-b:a", "64k"]
        head = ["-y", "-fflags", "+genpts", "-i", src, "-map", "0:v:0"]
        tail = ["-movflags", "+faststart", "-f", "mp4", tmp]
        attempts = [[*head, "-map", "0:a:0?", "-c:v", "copy", *audio, *tail],
                    [*head, "-an", "-c:v", "copy", *tail]]  # ohne Ton, falls der stört
        if transcode and not is_h264:
            attempts.insert(0, [*head, "-map", "0:a:0?", *x264, *audio, *tail])
        elif transcode:
            attempts.append([*head, "-map", "0:a:0?", *x264, *audio, *tail])
        err = ""
        for args in attempts:
            code, err = await self._ffmpeg(*args)
            if code == 0:
                await self.hass.async_add_executor_job(tmp.replace, dst)
                return True
        video = re.search(r"Video: [^\n]*", probe)
        _LOGGER.warning("ffmpeg konnte %s nicht umwandeln (%s): %s", src.name,
                        video.group(0)[:120] if video else "kein Videostrom", err.strip()[-400:])
        await self.hass.async_add_executor_job(tmp.unlink, True)
        return False

    def _keep_failed(self, ts: Path) -> None:
        """Gescheiterte Eingaben zur Analyse aufheben (höchstens ein paar)."""
        failed = self.work / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        if len(list(failed.iterdir())) < 5:
            ts.replace(failed / ts.name)
        else:
            ts.unlink(missing_ok=True)

    async def _ensure_thumb(self, event_id: str, video_rel: str, rel_dir: str, stem: str) -> None:
        if self.device.media.get(event_id, {}).get("thumb"):
            return
        rel = f"{rel_dir}/{stem}.jpg"
        code, _ = await self._ffmpeg("-y", "-ss", "1", "-i", self.root / video_rel, "-frames:v", "1",
                                     "-vf", "scale=640:-2", "-q:v", "4", self.root / rel, timeout=60)
        if code == 0:
            self.device.async_media_ready(event_id, "thumb", rel, {})

    # --- Aufräumen ------------------------------------------------------------------

    async def async_cleanup(self, drink_days: int, other_days: int, max_bytes: int) -> None:
        """Clips gezählter Trinkvorgänge `drink_days` Tage aufheben, alle übrigen `other_days`.
        Wird es insgesamt mehr als `max_bytes`, gehen zuerst die ältesten übrigen Clips."""
        known = {rel for entry in self.device.media.values() for rel in entry.values() if isinstance(rel, str)}
        keep_long = self.device.drink_media_rels()
        removed, gone, total, count = await self.hass.async_add_executor_job(
            self._cleanup_files, drink_days, other_days, max_bytes, keep_long, known)
        if removed or gone:
            self.device.async_media_removed(removed | gone)
        if removed:
            _LOGGER.info("Clips aufgeräumt: %d Dateien gelöscht, %.0f MB belegt", len(removed), total / 1e6)
        self.device.async_set_local({"clip_bytes": total, "clip_files": count})

    def _cleanup_files(self, drink_days: int, other_days: int, max_bytes: int, keep_long: set[str],
                       known: set[str]) -> tuple[set[str], set[str], int, int]:
        removed: set[str] = set()
        today = date.today()
        files: list[tuple[int, date, str, Path, int]] = []  # (übrig 0/Trinken 1, Tag, rel, Pfad, Größe)
        clips = self.root / "clips"
        if clips.is_dir():
            for day_dir in clips.iterdir():
                try:
                    day = date.fromisoformat(day_dir.name)
                except ValueError:
                    continue
                for f in day_dir.iterdir():
                    if not f.is_file():
                        continue
                    rel = f"clips/{day_dir.name}/{f.name}"
                    drink = rel in keep_long
                    if day < today - timedelta(days=drink_days if drink else other_days):
                        f.unlink(missing_ok=True)
                        removed.add(rel)
                    else:
                        files.append((int(drink), day, rel, f, f.stat().st_size))
        # Obergrenze: zuerst die ältesten übrigen Clips, dann die ältesten Trinkvorgänge (mit etwas Luft)
        total = sum(x[4] for x in files)
        if total > max_bytes:
            for drink, day, rel, f, size in sorted(files, key=lambda x: (x[0], x[1], x[2])):
                if total <= max_bytes * 0.9:
                    break
                f.unlink(missing_ok=True)
                removed.add(rel)
                total -= size
            _LOGGER.warning("Clips über der Obergrenze von %.1f GB: älteste gelöscht", max_bytes / 1e9)
        if clips.is_dir():
            for day_dir in clips.iterdir():
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
        # Liegengebliebenes (abgebrochene Uploads, nie gemeldete Dateien)
        stale = time.time() - 86400
        for folder in (self.incoming, self.work):
            if folder.is_dir():
                for f in folder.iterdir():
                    if f.is_file() and f.stat().st_mtime < stale:
                        f.unlink(missing_ok=True)
        gone = {rel for rel in known - removed if not (self.root / rel).exists()}
        return removed, gone, total, len(files) - len(removed & {x[2] for x in files})
