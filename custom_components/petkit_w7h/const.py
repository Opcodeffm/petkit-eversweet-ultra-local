"""Konstanten für die lokale Eversweet-Ultra-Integration."""

DOMAIN = "petkit_w7h"

CONF_PORT = "port"
CONF_PUBLIC_HOST = "public_host"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_SECRET = "device_secret"

# Optionen
CONF_PET_NAME = "pet_name"
CONF_CLIPS = "clips"
CONF_RETENTION_DAYS = "retention_days"              # Clips gezählter Trinkvorgänge
CONF_RETENTION_OTHER_DAYS = "retention_other_days"  # alle übrigen Clips (Besuche, Spülen, aussortiert)
CONF_MAX_STORAGE_GB = "max_storage_gb"              # Obergrenze für alle Clips zusammen
CONF_MESSAGE_LANGUAGE = "message_language"  # Sprache der Sätze wie "Luna hat 55 Sekunden getrunken"
CONF_ONLY_KNOWN = "only_known"  # nur Trinkvorgänge erkannter Tiere zählen/zeigen
CONF_DRINK_MERGE_MIN = "drink_merge_minutes"  # kurze Trinkpausen zu einem Trinkvorgang zusammenfassen
DEFAULT_PET_NAME = "Katze"  # Deutsch; sonst DEFAULT_PET_NAME_EN
DEFAULT_PET_NAME_EN = "Cat"
DEFAULT_RETENTION_DAYS = 30
# Gemessen: Pausen innerhalb einer Runde bis 8,5 min, zwischen Besuchen ab 20 min
DEFAULT_DRINK_MERGE_MIN = 10
DEFAULT_RETENTION_OTHER_DAYS = 3
DEFAULT_MAX_STORAGE_GB = 5

DEFAULT_PORT = 8098  # 8099 belegt oft das HA-Terminal-Add-on (ttyd)

# Der Brunnen meldet sich alle 10 s per Heartbeat.
ONLINE_TIMEOUT_S = 60

# Startwerte für die Einstellungen, falls der Speicher noch leer ist
# (z. B. aus der gesicherten Geräte-Config übernommen).
SETTINGS_IMPORT_FILE = "petkit_w7h_settings.json"

EVENT_BUS = f"{DOMAIN}_event"
EVENT_DRINK = f"{DOMAIN}_drink"  # Trinkvorgang beendet
EVENT_CLIP = f"{DOMAIN}_clip"    # Clip liegt bereit

PLATFORMS = ["binary_sensor", "button", "camera", "event", "image", "number", "select", "sensor", "switch", "time"]

# Clips: Ablage unter <media>/petkit_w7h, ausgeliefert über diese URLs
MEDIA_SUBDIR = "petkit_w7h"
MEDIA_URL = f"/api/{DOMAIN}/media"
STATIC_URL = f"/{DOMAIN}_static"

# Aufnahme-Kontingent, das wir dem Gerät melden (2037-12-31, bleibt unter 2^31)
CAPACITY_INDATE = 2145916799
CAPACITY_NAMES = ("fullVideo", "eventImage", "highLight", "dynamicVideo")

# Trinkvorgänge, die wir uns merken (ältere fallen raus)
DRINK_HISTORY_MAX = 2000

# event_type aus dev_event_report → interner Name
EVENT_TYPES = {
    1: "error_start",
    2: "error_over",
    3: "work_start",       # content.action: 1 Spülen, 2 Nachfüllen, 3 Ablassen, 4 Tiefenreinigung
    5: "drink_start",
    6: "drink_over",       # content.drink_time = Trinkdauer in s, content.pet_id
    8: "flush_over",
    9: "add_water_over",   # content.err z. B. "taryF"
    20: "pet_detected",
    24: "pet_identified",
}

# workState.workMode → laufende Arbeit
WORK_MODES = {0: "idle", 1: "flushing", 2: "refilling", 3: "draining", 4: "deep_cleaning"}

# start_action für den Dienst "start"
ACTION_DRAIN_AND_FLUSH = 1
ACTION_REFILL = 2
ACTION_DRAIN = 3
ACTION_DEEP_CLEAN = 4  # Reinigung mit kochendem Wasser (man gießt selbst nach)

ERROR_FLAGS = (
    "DC", "mcu", "rtc", "cameraL", "cameraE", "taryD", "taryL", "taryF", "taryO",
    "ptcL", "ptcM", "valveL", "valveE", "valveN", "cycL", "cycM", "repL", "repM",
)

# Störungen im Klartext. Der Brunnen meldet sie im Zustand (err.*) und als Störungs-Event
# (event 1/2, content.err). Bedeutung teils aus dem Verhalten erschlossen; wo unsicher,
# bleibt der Code in Klammern stehen.
ERROR_TEXT: dict[str, tuple[str, str]] = {
    "DC": ("Stromversorgung", "Power supply"),
    "mcu": ("Steuerplatine (MCU)", "Controller board (MCU)"),
    "rtc": ("Uhr (RTC)", "Clock (RTC)"),
    "cameraL": ("Kamera getrennt", "Camera disconnected"),
    "cameraE": ("Kamerafehler", "Camera error"),
    "cameraN": ("Kamera nicht verfügbar", "Camera unavailable"),
    "taryD": ("Wasserschale nicht eingesetzt", "Water tray not inserted"),
    "taryL": ("Wasserschale: zu wenig Wasser", "Water tray: water low"),
    "taryF": ("Wasserschale voll", "Water tray full"),
    "taryO": ("Wasserschale (taryO)", "Water tray (taryO)"),
    "ptcL": ("Heizung (ptcL)", "Heater (ptcL)"),
    "ptcM": ("Heizung defekt", "Heater malfunction"),
    "ptcU": ("Heizschale entnommen", "Heated tray removed"),
    "valveL": ("Hubventil (valveL)", "Lift valve (valveL)"),
    "valveE": ("Hubventil Fehler", "Lift valve error"),
    "valveN": ("Hubventil (valveN)", "Lift valve (valveN)"),
    "cycL": ("Umwälzpumpe (cycL)", "Circulation pump (cycL)"),
    "cycM": ("Umwälzpumpe defekt", "Circulation pump malfunction"),
    "repL": ("Nachfüllpumpe (repL)", "Refill pump (repL)"),
    "repM": ("Nachfüllpumpe defekt", "Refill pump malfunction"),
    "tankCU": ("Frischwassertank (tankCU)", "Clean water tank (tankCU)"),
    "tankDU": ("Abwassertank entnommen", "Waste water tank removed"),
    "tankCL": ("Frischwasser knapp", "Clean water low"),
    "tankDF": ("Abwassertank voll", "Waste water tank full"),
    "tankL": ("Tank: zu wenig Wasser (tankL)", "Tank: water low (tankL)"),
}

# Tank-Zustände als Klartext (Optionen der Enum-Sensoren)
CLEAN_TANK_STATES = ["ok", "low", "empty", "unknown"]
WASTE_TANK_STATES = ["ok", "full", "removed", "unknown"]
