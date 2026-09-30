"""SQLite storage. Parameterized queries only; bounded tables; WAL mode."""
import sqlite3, threading
from datetime import datetime
import pandas as pd
import config

TABLES = ("telemetry", "detections", "degradation_events", "system_events")
SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, pass_count INTEGER,
  temp_c REAL, sound REAL, speed_rpm REAL, temp_status TEXT, sound_status TEXT,
  speed_status TEXT, motor_state TEXT, safety_switch TEXT);
CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, pass_count INTEGER,
  detected_class TEXT, confidence REAL, x_pct REAL, bbox TEXT);
CREATE TABLE IF NOT EXISTS degradation_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, pass_count INTEGER,
  detected_class TEXT, confidence REAL, position TEXT, temperature REAL, acoustic REAL,
  speed REAL, degradation_score REAL, condition TEXT, motor_state TEXT);
CREATE TABLE IF NOT EXISTS system_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, level TEXT, component TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS ix_deg_src ON degradation_events(source, id);
CREATE INDEX IF NOT EXISTS ix_tel_src ON telemetry(source, id);
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path=config.DB_PATH):
        self.lock = threading.Lock()
        self.error = None
        self.ready = False
        self._writes = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
            self.ready = True
        except Exception as e:
            self.error = str(e)

    @property
    def status(self):
        return "READY" if self.ready and not self.error else "ERROR"

    def _exec(self, sql, params=()):
        if not self.ready:
            return
        try:
            with self.lock:
                self.conn.execute(sql, params)
                self.conn.commit()
                self._writes += 1
                if self._writes % 200 == 0:
                    self._prune()
            self.error = None
        except sqlite3.Error as e:
            self.error = str(e)

    def _prune(self):  # table names come from a constant whitelist
        for t in TABLES:
            self.conn.execute(
                f"DELETE FROM {t} WHERE id <= (SELECT COALESCE(MAX(id),0) FROM {t}) - ?",
                (config.MAX_ROWS_PER_TABLE,))
        self.conn.commit()

    def query(self, sql, params=()):
        if not self.ready:
            return pd.DataFrame()
        try:
            with self.lock:
                return pd.read_sql_query(sql, self.conn, params=params)
        except Exception as e:
            self.error = str(e)
            return pd.DataFrame()

    # ---- writers
    def log_telemetry(self, t, motor):
        self._exec("INSERT INTO telemetry(timestamp,source,pass_count,temp_c,sound,speed_rpm,"
                   "temp_status,sound_status,speed_status,motor_state,safety_switch) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (now_iso(), t["source"], t["pass_count"], t["temp_c"], t["sound"], t["speed_rpm"],
                    t["status"]["temp"], t["status"]["sound"], t["status"]["speed"], motor, t.get("safety_switch")))

    def log_detection(self, source, pass_count, d):
        self._exec("INSERT INTO detections(timestamp,source,pass_count,detected_class,confidence,x_pct,bbox) VALUES(?,?,?,?,?,?,?)",
                   (now_iso(), source, pass_count, d["cls"], d["conf"], d["x_pct"], str(d["bbox"])))

    def log_degradation(self, source, r, t, motor):
        pos = None if r["x_pct"] is None else f"{r['x_pct']:.0f}% of frame width"
        self._exec("INSERT INTO degradation_events(timestamp,source,pass_count,detected_class,confidence,position,"
                   "temperature,acoustic,speed,degradation_score,condition,motor_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (now_iso(), source, r["pass"], r["cls"], r["conf"], pos, t["temp_c"], t["sound"],
                    t["speed_rpm"], r["score"], r["condition"], motor))

    def log_event(self, level, component, message):
        self._exec("INSERT INTO system_events(timestamp,level,component,message) VALUES(?,?,?,?)",
                   (now_iso(), level, component, message))

    def clear_source(self, source):
        for t in ("telemetry", "detections", "degradation_events"):
            self._exec(f"DELETE FROM {t} WHERE source=?", (source,))

    # ---- readers
    def get_events(self, source, limit):
        return self.query("SELECT timestamp,pass_count,detected_class,confidence,position,temperature,acoustic,"
                          "speed,degradation_score,condition,motor_state FROM degradation_events "
                          "WHERE source=? ORDER BY id DESC LIMIT ?", (source, int(limit)))

    def get_telemetry(self, source, limit):
        return self.query("SELECT timestamp,pass_count,temp_c,sound,speed_rpm FROM telemetry "
                          "WHERE source=? ORDER BY id DESC LIMIT ?", (source, int(limit)))

    def get_scores(self, source, limit):
        df = self.query("SELECT pass_count,degradation_score,detected_class,confidence,position,condition,timestamp "
                        "FROM degradation_events WHERE source=? ORDER BY id DESC LIMIT ?", (source, int(limit)))
        return df.iloc[::-1].reset_index(drop=True)

    def get_system_events(self, limit):
        return self.query("SELECT timestamp,level,component,message FROM system_events ORDER BY id DESC LIMIT ?",
                          (int(limit),))
