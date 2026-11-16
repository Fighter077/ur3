"""Zentrale Konfiguration. Alles über Umgebungsvariablen überschreibbar (Docker-freundlich)."""
import os


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Server ---
    HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
    PORT = int(os.getenv("BACKEND_PORT", "8080"))

    # --- UR3e / RTDE ---
    # IP des Roboters im Netz. Bei MOCK egal.
    ROBOT_IP = os.getenv("ROBOT_IP", "192.168.1.100")
    # Ohne echten Roboter: Mock-Controller (simuliert Bewegungen, keine Hardware nötig).
    ROBOT_MOCK = _as_bool(os.getenv("ROBOT_MOCK"), default=True)
    # Geschwindigkeits-/Beschleunigungs-Defaults (SI: m/s, m/s^2 bzw. rad)
    ROBOT_TCP_SPEED = float(os.getenv("ROBOT_TCP_SPEED", "0.1"))
    ROBOT_TCP_ACCEL = float(os.getenv("ROBOT_TCP_ACCEL", "0.3"))
    ROBOT_JOINT_SPEED = float(os.getenv("ROBOT_JOINT_SPEED", "0.5"))
    ROBOT_JOINT_ACCEL = float(os.getenv("ROBOT_JOINT_ACCEL", "0.8"))
    # Sicherheits-Kraftschwelle [N] für die kraftgeregelte Entnahme / Handover-Stopp.
    ROBOT_FORCE_THRESHOLD = float(os.getenv("ROBOT_FORCE_THRESHOLD", "20.0"))
    # --- Greifer / Endeffektor ---
    # none    = nur Statusflag (Platzhalter)
    # digital = UR-Digital-Ausgang schalten (einfachste Hardware-Anbindung, z. B. Pneumatik/Relais)
    # robotiq = Robotiq-2F über das URCap-Socket (Port 63352) ansteuern
    GRIPPER_TYPE = os.getenv("GRIPPER_TYPE", "none")
    GRIPPER_DO_PIN = int(os.getenv("GRIPPER_DO_PIN", "0"))   # bei GRIPPER_TYPE=digital
    GRIPPER_DO_CLOSED = _as_bool(os.getenv("GRIPPER_DO_CLOSED"), default=True)  # HIGH = geschlossen?

    # --- Perception: nur jedes n-te Frame verarbeiten (CPU-Last begrenzen) ---
    DETECT_EVERY_N = int(os.getenv("DETECT_EVERY_N", "2"))

    # --- Hände / Finger (MediaPipe) -> Trigger-Geste ---
    # Der Arbeiter löst den Inspektionszyklus per Geste (oder Knopf) aus. MediaPipe Hands
    # liefert 21 Landmarks pro Hand -> Gesten (offen/Faust/Zeigen/Pinch).
    HANDS_ENABLED = _as_bool(os.getenv("HANDS_ENABLED"), default=True)
    HANDS_MAX = int(os.getenv("HANDS_MAX", "1"))
    HANDS_COMPLEXITY = int(os.getenv("HANDS_COMPLEXITY", "0"))  # 0 schnell, 1 genauer
    HANDS_MIN_CONF = float(os.getenv("HANDS_MIN_CONF", "0.5"))
    # MediaPipe-Tasks-Modell (wird im Dockerfile vorab geladen, sonst Laufzeit-Download).
    HANDS_MODEL_PATH = os.getenv("HANDS_MODEL_PATH", "models/hand_landmarker.task")
    # Diese Geste, stabil über N Verarbeitungs-Frames gehalten, startet den Zyklus.
    TRIGGER_GESTURE = os.getenv("TRIGGER_GESTURE", "open")
    TRIGGER_STABLE_FRAMES = int(os.getenv("TRIGGER_STABLE_FRAMES", "6"))

    # --- Optionale YOLO-Objekterkennung (z. B. "liegt ein Teil im Halter?") ---
    # Standardmäßig AUS: die eigentliche Vision-Aufgabe ist OCR, nicht Objektdetektion.
    OBJECTS_ENABLED = _as_bool(os.getenv("OBJECTS_ENABLED"), default=False)
    YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolo11n.pt")
    YOLO_CONF = float(os.getenv("YOLO_CONF", "0.5"))
    YOLO_IOU = float(os.getenv("YOLO_IOU", "0.45"))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")

    # --- OCR (Label lesen) ---
    OCR_ENABLED = _as_bool(os.getenv("OCR_ENABLED"), default=True)
    # Engine: "openai" (Vision-LLM, liest + parst Marke/Spezifikation direkt – braucht Internet +
    # OPENAI_API_KEY), "easyocr" (lokal, neuronal) oder "tesseract" (lokal, leicht).
    OCR_ENGINE = os.getenv("OCR_ENGINE", "easyocr")
    # OpenAI (nur bei OCR_ENGINE=openai). Key NICHT ins Repo – kommt aus .env/Umgebung.
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # vision-fähig; gpt-4o für max. Genauigkeit
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OCR_LANG = os.getenv("OCR_LANG", "eng")          # Tesseract-Sprachcode
    # EasyOCR-Modelle: Ablageordner (im gemounteten models/ -> persistent, 1. Start lädt ~100 MB).
    EASYOCR_DIR = os.getenv("EASYOCR_DIR", "models/easyocr")
    # Erkannte Textboxen unter dieser Konfidenz verwerfen.
    OCR_MIN_CONF = float(os.getenv("OCR_MIN_CONF", "0.3"))
    # Label im Bild automatisch finden, entzerren, zuschneiden, hochskalieren -> bessere OCR.
    LABEL_CROP = _as_bool(os.getenv("LABEL_CROP"), default=True)

    # --- Inventar / Digital Product Passport ---
    # Persistenter Speicher für Datensätze + Bilder (als Volume mounten, siehe podman-up.sh).
    DATA_DIR = os.getenv("DATA_DIR", "data")

    # --- Inspektion: gültige Bauteiltypen (eigener Halter-Insert je Typ) ---
    PART_TYPES = os.getenv("PART_TYPES", "HDD,optical,RAM").split(",")
    # Verweildauer [s] pro Inspektionspose: Zeitfenster, um das Label vor die Kamera zu halten
    # bzw. bis der Roboter wirklich still steht. Bewusst großzügig (ruhiger Ablauf, bessere OCR).
    INSPECT_SETTLE_S = float(os.getenv("INSPECT_SETTLE_S", "3.5"))
    # Kurze Pause [s] zwischen den Schritten, damit man die Aktion im Overlay lesen kann.
    STEP_PAUSE_S = float(os.getenv("STEP_PAUSE_S", "1.2"))
    # OCR über mehrere Frames im Zeitfenster versuchen und das beste Ergebnis nehmen.
    # (EasyOCR ist pro Frame treffsicherer als Tesseract -> weniger Versuche nötig.)
    OCR_ATTEMPTS = int(os.getenv("OCR_ATTEMPTS", "2"))
    # Datei mit eingeteachten Roboter-Posen (für den echten UR3e). Im gemounteten data/-Ordner,
    # damit geteachte Posen einen Neustart überleben. Fehlt sie -> Platzhalter aus dem Code.
    POSES_FILE = os.getenv("POSES_FILE", "data/poses.json")


config = Config()
