"""Heartbeat + motor/safety-switch interpretation. Prototype logic; NOT a certified safety system."""
import threading, time
import config


class SafetyMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self.last = None

    def beat(self, t=None):
        with self._lock:
            self.last = t or time.time()

    def reset(self):
        with self._lock:
            self.last = None

    def status(self, now):
        """-> (state, age_s). state: HEALTHY | TIMEOUT | NO DATA"""
        with self._lock:
            last = self.last
        if last is None:
            return "NO DATA", None
        age = now - last
        return ("HEALTHY" if age <= config.HEARTBEAT_TIMEOUT_S else "TIMEOUT"), age


def motor_state_of(tel):
    if not tel:
        return "UNKNOWN"
    if tel.get("motor"):
        return tel["motor"]
    sp = tel.get("speed_rpm")
    if sp is None:
        return "UNKNOWN"
    return "RUNNING (inferred)" if sp > 0 else "STOPPED (inferred)"


def switch_state_of(tel):
    if not tel or not tel.get("safety_switch"):
        return "UNKNOWN (not in telemetry)"
    return tel["safety_switch"]
