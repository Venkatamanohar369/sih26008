"""Progressive degradation scoring. Prototype logic only: NOT a rupture predictor, NOT an RUL estimate."""
from collections import deque
import numpy as np
import config

T = config.THRESHOLDS


def condition_of(score):
    if score >= T["critical_at"]:
        return "CRITICAL"
    return "WARNING" if score >= T["warning_at"] else "NORMAL"


def stage_of(score):
    if score >= T["critical_at"]:
        return "CRITICAL"
    if score >= T["warning_at"]:
        return "WARNING"
    return "ANOMALY" if score >= T["anomaly_at"] else "NORMAL"


class DegradationScorer:
    def __init__(self):
        self.ema = 0.0
        self.hist = deque(maxlen=config.HISTORY_POINTS)   # (pass, score)

    def reset(self):
        self.ema = 0.0
        self.hist.clear()

    def load(self, rows):
        self.reset()
        for p, s in rows:
            self.hist.append((int(p), float(s)))
        if self.hist:
            self.ema = self.hist[-1][1]

    @staticmethod
    def _pts(val, warn, crit, pts):
        if val is None:                      # failed sensor contributes nothing (and is shown as FAULT/N/A in UI)
            return 0.0
        return pts[1] if val >= crit else pts[0] if val >= warn else 0.0

    def instant(self, cls, conf, temp, sound):
        vision = config.CLASS_WEIGHTS.get(cls, 0.0) * (conf or 0.0)
        tp = self._pts(temp, T["temp_warn_c"], T["temp_crit_c"], T["temp_points"])
        sp = self._pts(sound, T["sound_warn"], T["sound_crit"], T["sound_points"])
        return min(100.0, vision + tp + sp)

    def trend(self):
        pts = list(self.hist)[-T["trend_window"]:]
        if len(pts) < 3 or len({p for p, _ in pts}) < 2:
            return "INSUFFICIENT DATA", None
        x, y = zip(*pts)
        slope = float(np.polyfit(x, y, 1)[0])
        if slope > T["trend_eps"]:
            return "INCREASING", slope
        if slope < -T["trend_eps"]:
            return "DECREASING", slope
        return "STABLE", slope

    def update(self, pass_count, cls, conf, temp, sound):
        inst = self.instant(cls, conf, temp, sound)
        a = T["ema_alpha"]
        self.ema = inst if not self.hist else a * inst + (1 - a) * self.ema
        self.hist.append((pass_count, self.ema))
        trend, slope = self.trend()
        return {"score": round(self.ema, 1), "instant": round(inst, 1), "trend": trend, "slope": slope,
                "condition": condition_of(self.ema), "stage": stage_of(self.ema)}
