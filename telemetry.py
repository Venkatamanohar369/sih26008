"""Telemetry parsing/validation, serial reader thread, and clearly-labelled DEMO generator."""
import json, math, random, threading, time
import config

# ---------------- parsing & validation ----------------
def parse_line(text):
    """-> (dict|None, error|None)"""
    s = text.strip()
    if not s:
        return None, "empty"
    if not s.startswith("{"):
        return None, "non-JSON line"
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        return None, f"malformed JSON: {e.msg}"
    if not isinstance(obj, dict):
        return None, "JSON is not an object"
    return obj, None


def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x) or x < lo or x > hi:
        return None
    return x


def normalize(raw, source):
    """Validate one payload. Failed sensors become None + 'FAULT'/'N/A'. Never a fake-normal value."""
    st_in = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    out = {"source": source, "rx": time.time(), "ts_dev": raw.get("ts"), "status": {}}
    spec = (("temp", "temp_c", -40, 125), ("sound", "sound", 0, 4095), ("speed", "speed_rpm", 0, 500))
    for key, field, lo, hi in spec:
        present = raw.get(field) is not None
        val = _num(raw.get(field), lo, hi)
        dev = str(st_in.get(key, "")).upper()
        if dev in ("FAULT", "ERROR", "FAIL"):
            out[field], out["status"][key] = None, "FAULT"
        elif not present:
            out[field], out["status"][key] = None, "N/A"
        elif val is None:                       # present but NaN / out of range / non-numeric
            out[field], out["status"][key] = None, "FAULT"
        else:
            out[field], out["status"][key] = val, "OK"
    pc = raw.get("pass_count")
    out["pass_count"] = int(pc) if isinstance(pc, (int, float)) and not isinstance(pc, bool) and pc >= 0 else None
    m = str(raw.get("motor", "")).upper()
    out["motor"] = m if m in ("RUNNING", "STOPPED") else None
    sw = raw.get("safety_switch")
    if isinstance(sw, bool):
        sw = "ENABLED" if sw else "DISABLED"
    sw = str(sw).upper() if sw is not None else None
    out["safety_switch"] = sw if sw in ("ENABLED", "DISABLED") else None
    return out


# ---------------- serial reader ----------------
class SerialReader(threading.Thread):
    def __init__(self, on_msg):
        super().__init__(daemon=True, name="serial")
        self.on_msg = on_msg
        self.enabled = False
        self.connected = False
        self.ser = None
        self.port = None
        self.rx_count = 0
        self.malformed = 0
        self.last_error = None
        self._wlock = threading.Lock()
        self.start()

    def set_enabled(self, on):
        self.enabled = on
        if not on:
            self._close()

    def _find_port(self):
        if config.SERIAL_PORT:
            return config.SERIAL_PORT
        from serial.tools import list_ports
        for p in list_ports.comports():
            if any(k in p.device for k in ("ttyUSB", "ttyACM", "COM")):
                return p.device
        return None

    def _open(self):
        try:
            import serial
            port = self._find_port()
            if not port:
                self.last_error = "no serial port found"
                return False
            self.ser = serial.Serial(port, config.SERIAL_BAUD, timeout=1)
            self.port, self.connected, self.last_error = port, True, None
            return True
        except Exception as e:
            self.last_error, self.connected, self.ser = f"open failed: {e}", False, None
            return False

    def _close(self):
        self.connected = False
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def write_line(self, line):
        with self._wlock:
            try:
                if self.ser and self.connected:
                    self.ser.write((line + "\n").encode())
                    return True
            except Exception as e:
                self.last_error = f"write failed: {e}"
        return False

    def run(self):
        while True:
            if not self.enabled:
                time.sleep(0.5)
                continue
            if self.ser is None and not self._open():
                time.sleep(2)
                continue
            try:
                line = self.ser.readline()          # 1 s timeout; silence is handled by the heartbeat monitor
                if not line:
                    continue
                raw, err = parse_line(line.decode("utf-8", errors="replace"))
                if err == "empty":
                    continue
                if err:
                    self.malformed += 1
                    self.last_error = err
                    continue
                self.rx_count += 1
                self.on_msg(normalize(raw, "LIVE"))
            except Exception as e:
                self.last_error = f"read failed: {e}"
                self._close()
                time.sleep(1)


# ---------------- DEMO generator (ALL output labelled source='DEMO') ----------------
class DemoGenerator:
    def __init__(self):
        self.inject_fault = False
        self.reset()

    def reset(self):
        self.pass_count = 0
        self._t_pass = time.time()

    def step(self, now, stopped):
        if not stopped and now - self._t_pass >= config.DEMO_PASS_PERIOD_S:
            self.pass_count += 1
            self._t_pass = now
        p = self.pass_count % 40
        raw = {
            "ts": int(now), "pass_count": self.pass_count,
            "temp_c": round(30 + self.pass_count * 0.05 + random.uniform(-0.4, 0.4), 1),
            "sound": int(400 + random.uniform(-40, 40) + (150 if p > 25 else 0)),
            "speed_rpm": 0 if stopped else round(28 + random.uniform(-1, 1), 1),
            "motor": "STOPPED" if stopped else "RUNNING", "safety_switch": "ENABLED",
            "status": {"temp": "OK", "sound": "OK", "speed": "OK"},
        }
        if self.inject_fault:
            raw["temp_c"], raw["status"]["temp"] = None, "FAULT"
        return normalize(raw, "DEMO")

    def detections(self, shape):
        """Simulated detections: healthy -> anomalies -> damage over a 40-pass cycle."""
        h, w = shape[:2]
        p = self.pass_count % 40
        if p < 8:
            cls = "healthy_splice"
        elif p < 20:
            cls = "splice_anomaly" if random.random() < (p - 8) / 12 + 0.3 else "healthy_splice"
        else:
            r = random.random(); q = (p - 20) / 20
            cls = "belt_damage" if r < q + 0.2 else "splice_anomaly"
        bw, bh = random.randint(60, 110), random.randint(50, 90)
        x1 = random.randint(20, w - bw - 20); y1 = random.randint(h // 4, 3 * h // 4 - bh)
        return [{"cls": cls, "conf": round(random.uniform(0.72, 0.96), 2),
                 "bbox": (x1, y1, x1 + bw, y1 + bh), "x_pct": round(100 * (x1 + bw / 2) / w, 1)}]
