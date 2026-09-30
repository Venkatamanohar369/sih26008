"""Single configuration section. All thresholds are PROTOTYPE DEFAULTS,
NOT industrial standards. Calibrate on your own rig."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent


def _p(v: str) -> Path:
    p = Path(v)
    return p if p.is_absolute() else BASE_DIR / p


def _b(k, d="0"):
    return os.getenv(k, d).strip().lower() in ("1", "true", "yes", "on")


APP_TITLE = "SIH26008 — Intelligent Conveyor Belt Health Monitoring"
DEFAULT_MODE = os.getenv("APP_MODE", "DEMO").upper()      # DEMO | LIVE

# ---- paths
DB_PATH = _p(os.getenv("DB_PATH", "data/belt_monitor.db"))
LOG_PATH = _p("logs/app.log")
MODEL_PRIMARY = _p(os.getenv("YOLO_MODEL_PRIMARY", "models/best_ncnn_model"))
MODEL_FALLBACK = _p(os.getenv("YOLO_MODEL_FALLBACK", "models/best.pt"))

# ---- serial / camera
SERIAL_PORT = os.getenv("SERIAL_PORT", "").strip()
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
CAMERA_FPS = 5
CAMERA_WIDTH, CAMERA_HEIGHT = 640, 480

# ---- YOLO
CLASS_NAMES = ["healthy_splice", "splice_anomaly", "belt_damage"]
YOLO_CONF = 0.35
YOLO_IMGSZ = 320
INFER_INTERVAL_S = 1.0            # controlled inference rate (Pi 4B)

# ---- timing / safety
STALE_AFTER_S = 3.0               # telemetry older than this => STALE
HEARTBEAT_TIMEOUT_S = 5.0         # no telemetry for this long => COMMUNICATION FAULT
ENABLE_STOP_COMMAND = _b("ENABLE_STOP_COMMAND", "0")
STOP_COMMAND_LINE = '{"cmd":"MOTOR_STOP"}'
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ---- storage / UI
UI_REFRESH_S = 2
TELEMETRY_LOG_INTERVAL_S = 2.0
MAX_ROWS_PER_TABLE = 5000
HISTORY_POINTS = 300
DEMO_PASS_PERIOD_S = 2.0   # demo only: fast enough to show NORMAL->CRITICAL in ~1-2 min

# ---- degradation scoring (prototype defaults)
CLASS_WEIGHTS = {"healthy_splice": 0.0, "splice_anomaly": 50.0, "belt_damage": 90.0}
CLASS_SEVERITY = {"healthy_splice": "NONE", "splice_anomaly": "MEDIUM", "belt_damage": "HIGH", "none": "NONE"}
THRESHOLDS = {
    "temp_warn_c": 40.0, "temp_crit_c": 55.0,          # DHT reading, degC
    "sound_warn": 600, "sound_crit": 800,              # raw ADC (0-4095) - needs calibration
    "speed_min_rpm": 10, "speed_max_rpm": 60,          # prototype motor range
    "temp_points": (5.0, 10.0),                        # added to instant score (warn, crit)
    "sound_points": (5.0, 10.0),
    "ema_alpha": 0.4,                                  # smoothing across passes
    "anomaly_at": 15.0, "warning_at": 35.0, "critical_at": 70.0,   # score cut-offs
    "trend_window": 8, "trend_eps": 1.0,               # points, score-per-pass
}
