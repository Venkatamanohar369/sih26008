"""YOLOv8n wrapper (Ultralytics, NCNN preferred). Failures are reported, never hidden."""
import threading
import cv2
import config

COLORS = {"healthy_splice": (80, 200, 80), "splice_anomaly": (0, 190, 255), "belt_damage": (60, 60, 255)}


class YoloDetector:
    def __init__(self):
        self.model, self.state, self.error = None, "NOT LOADED", None
        self._lock = threading.Lock()
        self._fails = 0

    def ensure_loaded(self):
        with self._lock:
            if self.model is not None:
                return
            try:
                from ultralytics import YOLO
            except Exception as e:
                self.state, self.error = "ERROR", f"ultralytics not importable: {e}"
                return
            for p in (config.MODEL_PRIMARY, config.MODEL_FALLBACK):
                if p.exists():
                    try:
                        self.model = YOLO(str(p), task="detect")
                        self.state, self.error = "READY", None
                        return
                    except Exception as e:
                        self.error = f"load failed ({p.name}): {e}"
            self.state = "ERROR"
            self.error = self.error or f"no model at {config.MODEL_PRIMARY} or {config.MODEL_FALLBACK}"

    def detect(self, frame):
        """-> (list[dict]|None, error|None). None means inference did NOT happen."""
        if self.model is None:
            return None, self.error or "model not loaded"
        try:
            r = self.model.predict(frame, imgsz=config.YOLO_IMGSZ, conf=config.YOLO_CONF, verbose=False)[0]
            h, w = frame.shape[:2]
            out = []
            for b in r.boxes:
                x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                out.append({"cls": r.names[int(b.cls[0])], "conf": round(float(b.conf[0]), 2),
                            "bbox": (x1, y1, x2, y2), "x_pct": round(100 * (x1 + x2) / 2 / w, 1)})
            self._fails, self.state, self.error = 0, "READY", None
            return out, None
        except Exception as e:
            self._fails += 1
            self.state, self.error = "ERROR", f"inference failed: {e}"
            return None, self.error


def draw_detections(frame, dets):
    img = frame.copy()
    for d in dets or []:
        x1, y1, x2, y2 = d["bbox"]
        c = COLORS.get(d["cls"], (200, 200, 200))
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 2)
        cv2.putText(img, f"{d['cls']} {d['conf']:.2f}", (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 2)
    return img
