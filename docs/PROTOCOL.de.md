# W7H (Eversweet Ultra) – lokales HTTP-Protokoll

Stand 2026-10-09, Firmware 456.

**Quellen:**
- eigene Mitschnitte zwischen Brunnen und Server
- statische Analyse der Firmware 456 (`ctrl`, `cloud`)
- localkit als Referenz für die Bedeutung einzelner Codes (siehe unten)

Umsetzung: `custom_components/petkit_w7h/` in diesem Repository.

Alle Pfade liegen unter `<apiServer>` = `http://<server>:<port>/6/`.

## Geräte → Server

### Start und Synchronisation

| Endpoint | Methode | Unsere Antwort |
|---|---|---|
| `w7h/dev_syncTime` | GET | `{"result": <unix-s>}` |
| `w7h/dev_signup?mac&sn&chipId&id&bt_mac&hardware&firmware&locale&timezone` | GET | `{"result":{id,mac,sn,secret,timezone,locale}}`, id und secret unverändert aus der Geräte-Config |
| `w7h/dev_only_iot_device_info_v2?sn` | GET | **Muss** einen `ali`-Block enthalten, sonst bleibt `ctrl` hängen. Dummy mit `mqttHost "127.0.0.1"` |
| `w7h/dev_serverinfo` | GET | `{apiServers:[<wir>], ipServers:[], dns:[], nextTick:3600, linked:1}` |
| `w7h/dev_device_info` | GET | `{id,mac,sn,secret,…,settings:{…app_conf…}, capacity:[…]}`. Der Brunnen **übernimmt** diese Settings. |
| `w7h/dev_ota_check` | POST | `{"result":{}}`, also kein Update |
| `w7h/dev_video_device_info` | GET | `{"result":{"agora":{"license":"","appId":""}}}` |
| `w7h/dev_multi_config`, `w7h/dev_ble_device`, `w7h/dev_discern_pic` | GET | siehe `server.py` der Integration |
| `w7h/dev_oss_sts_info_new_v2` | GET (jede Minute bis Erfolg, dann stündlich; Prozess `cloud`) | `{}` = keine Uploads, sonst Upload-Ziel, siehe „Clips“ |
| `w7h/dev_upload_file_info_v2` | POST `fileInfos=[…]` | `{"result":[]}`, siehe „Clips“ |
| `w7h/dev_upload_log_token` | GET (alle 5 min) | leere Zugangsdaten |

### Heartbeat

- Aufruf alle 10 s:
  `GET poll/w7h/heartbeat?pongokcnt=N&heap=0&rt=<ms seit Boot>&iotStatus=0`
- Antwort ohne Befehl: `{"result":[{"time":<ms>}]}`
- Antwort mit Befehlen: `{"result":[{"time":<ms>,"timestamp":<s>,"content":"<JSON-String>"}, …]}`
- ⚠️ Mit Befehlen dürfen **nur** solche Einträge im Array stehen. Ein
  zusätzlicher nackter `{"time"}`-Eintrag lässt `ctrl` abstürzen.
- `timestamp` muss steigen, sonst gilt die Nachricht als „consumed“.
- `time/1000` und `timestamp` dürfen maximal 60 s auseinanderliegen.

### Status (`w7h/dev_state_report`, POST)

- Body: `state=<JSON>` (nicht URL-kodiert!), parsen mit `raw_decode` ab `state=`.
- Auch Events und Fehler bringen einen `state`-Block mit.

| Feld | Bedeutung (laut localkit-Referenz und Namen) |
|---|---|
| `device.sw` | Gerät an |
| `device.drink_time`, `pet_time`, `pet_close_time` | Zeitstempel: Trinken erkannt, Tierkörper im Bild, Tier nah („Pet near“). 0 = gerade nichts. Alle drei sind auch bei Menschenhänden gesetzt |
| `wifi.rsq` | WLAN-RSSI |
| `err.{DC,mcu,rtc,cameraL,cameraE,taryD,taryL,taryF,taryO,ptcL,ptcM,valveL,valveE,valveN,cycL,cycM,repL,repM}` | Fehler-Flags |
| `cameraStatus`, `heatState`, `liftValveState`, `pumpState`, `waterPumpState`, `cwtState`, `wtState`, `addWaterState`, `flushState`, `liftResetState`, `liftLiveState`, `disinfectState` | Laufzustände |
| `stgInstall`, `stgFullState`, `cwtInstall`, `wtInstall`, `wtLock`, `heatInstall` | Einbau- und Füllzustände (Tanks, Heizung) |
| `heatRealTemp`, `heatLeftTime`, `heatStatusTime`, `disinfectTime` | Heizung und Desinfektion |
| `addWaterFrequent` | Nachfüllen zu häufig |
| `sensor.hall_*` | Hall-Sensoren |
| `runtime`, `mem`, `cpu`, `reboot_reason` | Diagnose |
| `other` | String: u. a. `Ip:"…"`, Nachtsicht-Auswertung |

### Events (`w7h/dev_event_report`, POST)

- Body: `event_type=N&event_id=…&timestamp=…&content=<JSON>&state=<JSON>`

| event_type | Bedeutung | content |
|---|---|---|
| 20 | Haustier erkannt (pet_detect), mit Medien | `{mark,start_time,upload,media,petDetection}` |
| 24 | Haustier-Zuordnung (pet_discern) | `{related_event:<id von 20>,count,area,pet_id,tracker_info,vomit_info}`; `pet_id 0` = unbekannt. `related_event` kommt **ohne Anführungszeichen** |
| 1 / 2 | Fehler Start / Ende | `{err:"cameraL",msg,detail}`, Ende zusätzlich `start_time` |
| 3 | work_start | `{pos,reason,item_id,action}`; `reason` 0/1 automatisch, ab 2 von Hand (App oder Taste) |
| 5 / 6 | drink_start / drink_over | `{event_start}` bzw. `{mark,start_time,event_start,event_end,drink_time,media,upload,drinkDetection,pet_id,tracker_info,vomit_info}` |
| 8 / 9 | flush_over / add_water_over | `{start_time,event_start,event_end,start_reason,result,err,…}`; `result` siehe unten |

### `result` in `flush_over` / `add_water_over`

Bedeutung laut Petkit-App:

| result | Bedeutung |
|---|---|
| 0, 1 | fertig |
| 2 | Gerätefehler |
| 3 | abgebrochen |
| 4 | Wasserschale entnommen |
| 5 | Abwassertank entnommen |
| 6 | Frischwassertank leer |
| 7 | Abwassertank voll |
| 8 | Hubventil |
| 9 | Wasserschale voll |
| 10 | Nicht-stören-Zeit |
| 11 | Kamera aus |
| 12 | abgebrochen, weil ein Tier trinkt |
| 13 | Nachfüllen fehlgeschlagen |
| 14 | Hubventil oben |
| 15 | kein kochendes Wasser eingefüllt |
| 16 | Umwälzpumpe |

`start_reason` bzw. `reason`: 0 und 1 = automatisch, ab 2 von Hand (App oder Taste).

## Server → Gerät (per Heartbeat-`content`)

### Einstellungen

- Format: `{"msgType":1,"type":"property_set","payload":{<key>:<wert>,…}}`
- Live bestätigt mit `cameraLight`.
- Die Schlüssel entsprechen `app_conf` (Abschnitt `app_conf` der Gerätekonfiguration), zum Beispiel:
  - Brunnen: `fountainMode`, `fountainTime`, `sleepTime`, `heaterSwitch`, `targetTemp`
  - Wasser: `addWaterSwitch`, `addWaterMode`, `autoWaterChange`, `waterChangeCycle`, `waterChangeTime`, `autoFlush`, `flushCycle`, `flushTime`, `flushIntensity`
  - Lichter: `cameraLight`, `microLight`, `lightMode`, `lightMultiRange`, `cleanWaterLackLight`, `cleanWaterEmptyLight`, `wasteWaterFullLight`
  - Nicht stören: `wlDisturbMode`, `toneMode`, `awDisturbMode`, `disturbMode`
  - Kamera und Erkennung: `camera`, `microphone`, `night`, `petDetection`, `drinkDetection`, `smartFrame`
  - Ton und Sonstiges: `soundEnable`, `systemSoundEnable`, `volume`, `manualLock`

### Aktionen

- Format: `{"msgType":2,"type":"<dienst>","payload":{…}}`
- Parser: `parse_service_invoke_msg`.

| type | payload | Hinweis |
|---|---|---|
| `start` | `start_action`: 1 = Ablassen und Spülen, 2 = Nachfüllen, 3 = Ablassen, 4 = Tiefenreinigung | Codes laut localkit (MQTT-Mitschnitt) |
| `stop` / `continue` / `end` | `stop_action` / `continue_action` / `end_action` | |
| `power` | `power_action` | |
| `play_sound` | `soundId` | |
| `clear_cwt_light` | – | Frischwasser-LED quittieren |
| `add_water_Reset` | – | Nachfüll-Erinnerung zurücksetzen |
| `privacy` | `privacy_action` | |
| `discern` | – | Haustierfotos neu laden |
| weitere | – | `reset_pet`, `lapse`, `temp_report`, `reset_cycle_pump`, `reset_lift_valve`, `clear_softstate`, `open_camera`, `start_live`/`start_rtm` (Agora, nicht genutzt) |

## Clips (Objektspeicher-Emulation, Prozess `cloud`)

Bestätigt am 2026-10-09 per Ghidra (`cloud`, FW 456) und live.

1. **Zugangsdaten:** `dev_oss_sts_info_new_v2` →
   `{"result":{"type":"oci","capability":[…]}}`. `type` muss `oci` oder `ali` sein.
   Pro `cycleType` ein Eintrag. Ohne `eventImage`, `dynamicVideo` **und**
   `fullVideo` meldet das Gerät „Token update failed“.
   - Pflichtfelder je Eintrag: `pathPrefix` (**höchstens 16 Zeichen**,
     `strncpy`), `cycleExpiration` (s), `cycleType`, `isHD`, `cycle`.
   - Dazu je für `primary…` und `standby…`: `AesKeyStr`, `AesKeyUri`,
     `BucketName`, `Domain`, `ParUrl`, `ParExpiration` (ms), `AccessKeyId`,
     `AccessKeySecret`, `SecurityToken`.
   - `primaryAesKeyStr` muss gleich `standbyAesKeyStr` sein: 16 ASCII-Zeichen,
     die direkt als AES-128-Schlüssel dienen.
   - `…Domain` wird mit `sscanf("https://%[^/]/%s")` zerlegt und landet nur in
     `fileUrl` (kosmetisch). `http://` geht, dann bleibt der Host leer.
2. **Upload:** `PUT <primaryParUrl><pathPrefix>/<fileId>`, Body =
   AES-128-CBC-verschlüsselte Datei. Das Gerät erwartet HTTP 200. Das Schema
   übernimmt es aus `ParUrl`, also geht `http://` problemlos.
3. **Meldung:** `POST dev_upload_file_info_v2`, Body `fileInfos=<JSON-Array>`.
   - Felder: `fileId`, `deviceId`, `eventId`, `moduleType`, `fileType`,
     `startTime`, `endTime`, `duration`, `storageSpace`, `aesIv`.
   - Dazu die Flags `drinkEvent`, `petEvent`, `cvrEvent`, `flushEvent`,
     `addWaterEvent` sowie `mark`, `brightness`, `petScore`, `petTime` und
     `encrypt`.
   - `aesIv` ist fest `0x6161…61` („aaaaaaaaaaaaaaaa“), das Gerät setzt den IV
     nach jedem Token mit `memset(0x61)`.
4. **Zuordnung:** Die `eventId` der Uploads ist die `event_id` des auslösenden
   Events. Ein Trinkvorgang hat z. B. `2_12345678_1791529445` in event 5/6, die
   Endung ist die Startzeit.
5. **Kontingent:** Aufgenommen wird nur, solange `capacity[].indate` in der
   Zukunft liegt. Live per `property_set {"capacity":[…]}` änderbar: `ctrl`
   loggt `pk_device_parse_capacity_info … indate[n]`. Wir melden 2145916799.

Umsetzung: `custom_components/petkit_w7h/media.py`. Idee und OCI-Antwort
nach localkit (`DeviceObjectStorage.php`), die Details gegen das W7H-Binary
geprüft.

## Tiererkennung (`dev_discern_pic`)

Bestätigt am 2026-10-09 per Ghidra (`ctrl` face_info_ctrl.c, `/alg/libalgo.so`).

- **Antwort:**
  `{"result":{"list":[{"id":<petId>,"color":"#RRGGBB","petName":"…","petNamePicUrl":"…","discern":[{"id":<faceId>,"url":"http://…"}]}]}}`.
  - Höchstens 20 Tiere mit je 6 Fotos. `petName` hat bis zu 63 Zeichen,
    `petNamePicUrl` ist optional.
  - Eine leere Liste löscht alle Merkmale.
- **Neu holen** per `service_invoke`, Typ `discern`, Payload
  `{"discern_get":1}`. Ohne `discern_get` passiert nichts.
- **Download:** per wget.
  - Aus `https://` macht das Gerät `http://`.
  - Die URL sollte nur einfache Zeichen enthalten (kein `&`, `?` oder Leerzeichen).
- **Merkmale:** `CPetkitAlgoPetfeat::petkit_from_url_detect_feature`.
  - `cv::imread` liest das Bild und nimmt es **nur bei exakt 224×224**.
  - Eine Gesichtssuche gibt es dabei nicht: Das Bild geht direkt ins
    Merkmalsmodell `petkit_face_rec_mtl_s2_v5_sim`.
  - Bei einem Qualitätswert unter 0,5 wird das Foto verworfen.
  - Ergebnis: `/opt/feature.bin` und `/opt/pet_name_color.json`.
- **Erkennung live:**
  - Ablauf: Körper (`petkit_pet_1class`) → Gesicht und Keypoints
    (`cat_dog_mv2…224x224`) → Merkmal → Abgleich.
  - Schwellen aus `dev_discern_config`: `area` 6000, `score` 25.0.
  - Events melden `pet_id` (0 = kein Treffer): `drink_over` direkt,
    `pet_identified` (24) über `related_event`.
- **Trinken** erkennt ein eigenes Modell (`mobilenetv2_cat_drink_v0.9`),
  nicht die Bewegungserkennung.

## Referenz

Die Code-Bedeutungen (`start_action`, Event-Zuordnung, Feldnamen) stammen
teilweise aus localkit (github.com/dwyschka/localkit). Von dort wurde kein
Code übernommen.
