"""Background pipeline (one instance per process): DETECT -> LOCALIZE -> TRACK -> ASSESS -> TREND -> ACT.
The Streamlit UI only reads get_state(); it never touches serial/camera/model directly."""
import logging, threading, time
from logging.handlers import RotatingFileHandler
import config
from database import Database
from camera import CameraStream, synthetic_frame
from yolo_detector import YoloDetector, draw_detections
from telemetry import SerialReader, DemoGenerator
from degradation import DegradationScorer, condition_of, stage_of
from safety import SafetyMonitor, motor_state_of, switch_state_of

config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("engine")
if not log.handlers:
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(config.LOG_PATH, maxBytes=1_000_000, backupCount=3))


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.mode = config.DEFAULT_MODE if config.DEFAULT_MODE in ("DEMO", "LIVE") else "DEMO"
        self.db = Database()
        self.detector = YoloDetector()
        self.camera = CameraStream()
        self.serial = SerialReader(self._on_serial)
        self.demo = DemoGenerator()
        self.scorer = DegradationScorer()
        self.safety = SafetyMonitor()
        self.stop_latched, self.stop_sent = False, False
        self.last_infer = self.last_tel_log = 0.0
        self._apply_mode()
        threading.Thread(target=self._loop, daemon=True, name="engine").start()

    # ---------- mode handling ----------
    def _apply_mode(self):
        with self.lock:
            self.tel = None
            self.frame = self.frame_ts = self.frame_src = None
            self.dets, self.det_err = [], None
            self.window, self.window_frames = [], 0
            self.last_pass, self.last = None, None
            self.stop_latched = self.stop_sent = False
            self.safety.reset()
            live = self.mode == "LIVE"
            self.camera.set_enabled(live)
            self.serial.set_enabled(live)
            if live:
                threading.Thread(target=self.detector.ensure_loaded, daemon=True).start()
            self._restore_history()
            self.db.log_event("INFO", "engine", f"mode set to {self.mode}")

    def _restore_history(self):
        df = self.db.get_scores(self.mode, config.HISTORY_POINTS)
        self.scorer.load(list(zip(df["pass_count"], df["degradation_score"])) if not df.empty else [])
        if df.empty:
            return
        r = df.iloc[-1]
        score = float(r["degradation_score"])
        trend, slope = self.scorer.trend()
        self.last = {"pass": int(r["pass_count"]), "cls": r["detected_class"], "conf": r["confidence"],
                     "x_pct": None, "position": r["position"], "score": score, "instant": None,
                     "trend": trend, "slope": slope, "condition": r["condition"], "stage": stage_of(score),
                     "severity": config.CLASS_SEVERITY.get(r["detected_class"], "UNKNOWN"),
                     "ts": r["timestamp"], "restored": True}

    def set_mode(self, mode):
        if mode in ("DEMO", "LIVE") and mode != self.mode:
            self.mode = mode
            self._apply_mode()

    def reset_demo(self):
        with self.lock:
            self.db.clear_source("DEMO")
            self.demo.reset()
            self._apply_mode()

    def ack_stop(self):
        with self.lock:
            self.stop_latched = self.stop_sent = False
            self.db.log_event("INFO", "safety", "stop latch acknowledged by operator")

    # ---------- inputs ----------
    def _on_serial(self, tel):
        with self.lock:
            self.tel = tel
        self.safety.beat()

    # ---------- main loop ----------
    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                log.exception("tick failed")
                self.db.log_event("ERROR", "engine", f"tick failed: {e}")
            time.sleep(0.5)

    def _tick(self):
        now = time.time()
        if self.mode == "DEMO":
            tel = self.demo.step(now, self.stop_latched)
            with self.lock:
                self.tel = tel
            self.safety.beat()
        with self.lock:
            tel = self.tel
        if tel and now - self.last_tel_log >= config.TELEMETRY_LOG_INTERVAL_S:
            self.last_tel_log = now
            self.db.log_telemetry(tel, motor_state_of(tel))
        if now - self.last_infer >= config.INFER_INTERVAL_S:
            self.last_infer = now
            self._vision_step(now, tel)
        if tel:
            self._track_pass(tel)

    def _vision_step(self, now, tel):
        if self.mode == "DEMO":
            frame, src = synthetic_frame(), "SIMULATED"
            dets, err = self.demo.detections(frame.shape), None
        else:
            frame, src = self.camera.get(), "CAMERA"
            if frame is None:
                dets, err = None, f"camera offline: {self.camera.error or 'no recent frame'}"
            else:
                dets, err = self.detector.detect(frame)
        with self.lock:
            self.det_err = err
            self.frame_src = src
            if frame is not None:
                self.frame, self.frame_ts = draw_detections(frame, dets), now
            else:
                self.frame = None
            self.dets = dets or []
            if dets is not None:                       # only count frames that were genuinely inferred
                self.window.extend(dets)
                self.window_frames += 1

    def _track_pass(self, tel):
        pc = tel["pass_count"]
        if pc is None:
            return
        if self.last_pass is None:
            self.last_pass = pc
        elif pc < self.last_pass:
            self.db.log_event("WARN", "telemetry", f"pass_count went backwards ({self.last_pass}->{pc}); baseline reset")
            self.last_pass = pc
        elif pc > self.last_pass:
            self._on_pass(pc, tel)
            self.last_pass = pc

    def _on_pass(self, pc, tel):
        with self.lock:
            window, frames = self.window, self.window_frames
            self.window, self.window_frames = [], 0
        if frames == 0:
            self.db.log_event("WARN", "vision", f"pass {pc} NOT scored: no valid vision frames (camera/YOLO fault)")
            return
        best = max(window, key=lambda d: config.CLASS_WEIGHTS.get(d["cls"], 0) * d["conf"], default=None)
        cls = best["cls"] if best else "none"
        conf = best["conf"] if best else None
        x_pct = best["x_pct"] if best else None
        res = self.scorer.update(pc, cls, conf, tel["temp_c"], tel["sound"])
        res.update({"pass": pc, "cls": cls, "conf": conf, "x_pct": x_pct,
                    "position": None if x_pct is None else f"{x_pct:.0f}% of frame width",
                    "severity": config.CLASS_SEVERITY.get(cls, "UNKNOWN"),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        motor = motor_state_of(tel)
        if best:
            self.db.log_detection(self.mode, pc, best)
        self.db.log_degradation(self.mode, res, tel, motor)
        prev = self.last["condition"] if self.last else "NORMAL"
        with self.lock:
            self.last = res
            if res["condition"] == "CRITICAL" and not self.stop_latched:
                self.stop_latched = True
                self._send_stop()
        if res["condition"] != prev:
            self.db.log_event("WARN", "assess", f"condition {prev} -> {res['condition']} at pass {pc} (score {res['score']})")

    def _send_stop(self):
        note = "MOTOR STOP COMMAND raised (CRITICAL)"
        if self.mode == "LIVE" and config.ENABLE_STOP_COMMAND:
            self.stop_sent = self.serial.write_line(config.STOP_COMMAND_LINE)
            note += " - serial write ok" if self.stop_sent else " - serial write FAILED"
        else:
            note += " - advisory only, not transmitted"
        self.db.log_event("CRIT", "safety", note)

    # ---------- UI snapshot ----------
    def get_state(self):
        now = time.time()
        with self.lock:
            tel = dict(self.tel) if self.tel else None
            live = self.mode == "LIVE"
            hb, hb_age = self.safety.status(now)
            tel_age = now - tel["rx"] if tel else None
            cam_ok = (self.frame is not None) if live else True
            esp_ok = self.serial.connected and hb == "HEALTHY"
            if not live:
                stop_note = "simulated - no real motor"
            elif config.ENABLE_STOP_COMMAND:
                stop_note = "sent over serial to ESP32" if self.stop_sent else "serial write FAILED / pending"
            else:
                stop_note = "advisory only - NOT transmitted (ENABLE_STOP_COMMAND=0)"
            return {
                "mode": self.mode, "now": now, "tel": tel, "tel_age": tel_age,
                "stale": bool(tel_age is not None and tel_age > config.STALE_AFTER_S),
                "hb": hb if live else "SIMULATED", "hb_age": hb_age, "hb_fault": live and hb != "HEALTHY",
                "camera": ("ONLINE" if cam_ok else "OFFLINE") if live else "SIMULATED",
                "camera_err": self.camera.error,
                "yolo": self.detector.state if live else "SIMULATED", "yolo_err": self.detector.error,
                "esp32": ("ONLINE" if esp_ok else "OFFLINE") if live else "SIMULATED",
                "serial": ("CONNECTED" if self.serial.connected else "DISCONNECTED") if live else "N/A (demo)",
                "serial_port": self.serial.port, "serial_err": self.serial.last_error,
                "malformed": self.serial.malformed, "rx_count": self.serial.rx_count,
                "db": self.db.status, "db_err": self.db.error,
                "motor": ("STOP REQUESTED" if self.stop_latched else motor_state_of(tel)),
                "motor_raw": motor_state_of(tel), "switch": switch_state_of(tel),
                "frame": self.frame, "frame_ts": self.frame_ts, "frame_src": self.frame_src,
                "dets": list(self.dets), "det_err": self.det_err,
                "last": dict(self.last) if self.last else None, "hist": list(self.scorer.hist),
                "stop_latched": self.stop_latched, "stop_note": stop_note,
                "window_frames": self.window_frames, "last_pass_seen": self.last_pass,
            }
