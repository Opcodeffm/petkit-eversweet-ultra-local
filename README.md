# Petkit Eversweet Ultra (W7H) – local Home Assistant integration

Run the PETKIT Eversweet Ultra camera fountain (model W7H) fully locally with Home
Assistant. No Petkit cloud, no MQTT broker, no firmware patches: the fountain talks
plain HTTP to a small server inside this integration.

> Not affiliated with PETKIT. Use at your own risk. Tested with one fountain on
> firmware 456 and Home Assistant 2026.10.

*Deutsch: siehe [unten](#deutsch).*

## Features

- **Drinks:** every drink with duration, e.g. "Luna drank for 55 seconds", drinks and
  drinking time per day, an event for automations and a dashboard card with the last
  clip and the latest drinks.
- **Clips:** the fountain's event videos and snapshots are stored locally in your
  media folder (H.264 MP4). Retention is configurable, with a storage limit.
- **Pet recognition:** put 3–6 face photos per pet into a media folder. The fountain
  learns them and reports which pet was at the fountain.
- **False alarms filtered:** drinks detected while someone is handling the fountain
  (tray or tank out/in, buttons pressed) are ignored automatically.
- **Status in plain text:** clean and waste water tank, errors, current task, cube
  (filter) days left, water temperature with the heated tray. Separate sensors for
  "waste water tank full", "clean water low" and "clean water tank empty" make
  automations easy. (The W7H itself cannot tell whether the clean water tank is in
  place, only the waste water tank and the tray.)
- **Control:** refill, drain, drain & flush, deep clean, flow mode, schedules, quiet
  hours, lights, camera, detection settings and more (over 100 entities).

## Requirements

- Eversweet Ultra (W7H), set up once with the Petkit app so it is on your Wi-Fi.
- Home Assistant 2025.3 or newer, reachable from the fountain on TCP port 8098
  (configurable).
- Telnet access to the fountain as `root`. The login is described in the
  [localkit documentation](https://localkit.io/devices/eversweet-ultra), section
  "How to Access". No soldering or opening required.

## Installation

1. **HACS:** *HACS › ⋮ › Custom repositories*, add this repository as type
   *Integration*, install **Petkit Eversweet Ultra (local)** and restart Home Assistant.
   Manual alternative: copy `custom_components/petkit_w7h` into your `config/custom_components`.
2. **Add the integration:** *Settings › Devices & services › Add integration ›
   Petkit Eversweet Ultra*. Check the IP of Home Assistant and leave device ID and
   secret empty.
3. **Connect the fountain:** Home Assistant now shows a one-line command (also in your
   notifications). Log in to the fountain via telnet as `root` and run it:

   ```sh
   wget -qO- http://<home-assistant-ip>:8098/setup.sh | sh
   ```

   The script backs up the original configuration to `/opt/user.conf.stock`, hands
   device ID and secret to Home Assistant, points the fountain to Home Assistant and
   reboots it. About a minute later the fountain shows up.

   Check first without changing anything: `… | sh -s -- --dry-run`

4. **Recommended:** block internet access for the fountain in your router. It does not
   need it anymore.

### Back to the Petkit cloud

```sh
wget -qO- http://<home-assistant-ip>:8098/setup.sh | sh -s -- --cloud
```

This restores the original configuration (including the settings stored in it) and
reboots.

### Manual setup

If you already know device ID (`dev_id`) and secret (`dev_srt`) from
`/opt/user.conf`, you can enter them in step 2. The fountain then still needs to be
pointed to `http://<home-assistant-ip>:8098/6/` (the setup script does exactly that).

## Usage

### Dashboard card

The card is registered automatically:

```yaml
type: custom:petkit-drink-card
entity: sensor.eversweet_ultra_last_drink   # the "Last drink" sensor
title: Fountain       # optional
items: 6              # optional, entries in the list
show_list: true       # optional
```

It plays the clip of the latest drink (muted, looped) and lists the latest drinks.
Tap an entry to play its clip.

### Pet recognition

Upload 3–6 photos of each pet's face (frontal, filling the frame) to
*Media › My media › petkit_w7h › pets › &lt;Name&gt;*, one folder per pet. They are
cropped to 224×224 and handed to the fountain within 10 minutes. With a single pet,
every drink is attributed to it even when the face is not recognized.

### Options

*Settings › Devices & services › Eversweet Ultra › Configure*:

| Option | Default | Meaning |
|---|---|---|
| Pet name(s) | Cat / Katze | comma-separated, one folder per name for the photos |
| Language of drink messages | from HA | German or English |
| Only count drinks recognized by face | off | otherwise every drink counts unless someone was handling the fountain |
| Combine drinking breaks up to | 10 min | pet drinks, walks off briefly, drinks again = one drink (0 = off) |
| Receive clips | on | store videos and snapshots |
| Keep drink clips | 30 days | clips that belong to a counted drink |
| Keep other clips | 3 days | pet visits without drinking, flushing, water changes, removed drinks |
| Storage limit | 5 GB | above it the oldest other clips go first, then the oldest drink clips |

### Live view (experimental)

The fountain has no local live stream in normal operation; the Petkit app streams via the
Agora cloud. The firmware does contain a test program from production (`/app/bin/tserver`)
that serves the camera as HTTP-FLV (1080p, 25 fps, with sound) on port 80 when started.
The integration has a camera entity for it. It is disabled by default and only becomes
available while `tserver` runs on the fountain. Note: `tserver` has no password, anyone in
your network can open the stream. Starting it automatically is not part of the setup
script yet.

### Events and services

- `petkit_w7h_drink`: a counted drink (pet, duration, message, clip URL).
- `petkit_w7h_clip`: a clip is ready (also as event type `clip_ready` of the drink event entity).
- `petkit_w7h_event`: every raw event of the fountain.
- `petkit_w7h.ignore_drink`: remove drinks from statistics (or count them again).
- `petkit_w7h.invoke` / `petkit_w7h.set_settings`: raw actions and settings (advanced).

A drink shows up about one minute after it ends, after checking that nobody was
handling the fountain. Short breaks are combined: the `petkit_w7h_drink` event fires once
per drink, when no further round followed within the break time (default 10 minutes).

## How it works

The fountain stores its server addresses in an encrypted config file
(`/opt/user.conf`, edited with the device's own `pktool`). The setup script points
`apiServers` to Home Assistant. The integration answers the device API (heartbeat,
state and event reports, settings), emulates the object storage the fountain uploads
clips to, and serves the reference photos for pet recognition. Details:
[docs/PROTOCOL.de.md](docs/PROTOCOL.de.md) (German).

Firmware updates are not offered while the fountain runs locally. The live view of the
Petkit app (Agora) is not available locally, see "Live view" above.

## Credits

The idea of running Petkit devices locally, and parts of the interpretation of codes
and the object storage answer, come from [localkit](https://github.com/dwyschka/localkit)
by dwyschka. No code was copied. This integration avoids MQTT and binary patches and
was rebuilt against the W7H firmware.

## License

MIT, see [LICENSE](LICENSE).

---

## Deutsch

Lokale Home-Assistant-Integration für den PETKIT Eversweet Ultra (W7H): ohne
Petkit-Cloud, ohne MQTT, ohne Firmware-Patch. Trinkvorgänge mit Dauer und Clip,
Tiererkennung per eigener Fotos, Fehlalarm-Filter, Tank- und Fehlerstatus im Klartext
und eine Dashboard-Karte. Die Oberfläche der Integration ist deutsch und englisch.

**Einrichtung kurz:**
1. Über HACS installieren (benutzerdefiniertes Repository) und HA neu starten.
2. Integration hinzufügen, Geräte-ID und Secret leer lassen.
3. Per Telnet als `root` auf den Brunnen (Login siehe
   [localkit-Doku](https://localkit.io/devices/eversweet-ultra), „How to Access“)
   und die angezeigte Zeile ausführen:
   `wget -qO- http://<HA-IP>:8098/setup.sh | sh`
   Nur prüfen: `… | sh -s -- --dry-run`. Zurück zur Cloud: `… | sh -s -- --cloud`.
4. Empfohlen: dem Brunnen im Router den Internetzugang sperren.

Getestet mit einem Brunnen, Firmware 456. Keine Verbindung zu PETKIT, Nutzung auf
eigene Gefahr.
