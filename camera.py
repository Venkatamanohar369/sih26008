"""Camera thread with controlled frame rate + reconnect; synthetic frame for DEMO."""
import platform, threading, time
import cv2, numpy as np
import config


class CameraStream(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="camera")
        self.enabled = False
        self.error = None
        self._frame, self._ts = None, None
        self._lock = threading.Lock()
        self._cap = None
        self.start()

    def set_enabled(self, on):
        self.enabled = on
        if not on:
            self._release()

    def _release(self):
        try:
            if self._cap:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        with self._lock:
            self._frame = None

    def _open(self):
        api = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        cap = cv2.VideoCapture(config.CAMERA_INDEX, api)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def run(self):
        fails = 0
        while True:
            if not self.enabled:
                time.sleep(0.5)
                continue
            try:
                if self._cap is None:
                    self._cap = self._open()
                    if self._cap is None:
                        self.error = f"cannot open camera index {config.CAMERA_INDEX}"
                        time.sleep(3)
                        continue
                ok, f = self._cap.read()
                if not ok or f is None:
                    fails += 1
                    if fails >= 5:
                        self.error = "frame grab failed"
                        self._release()
                        fails = 0
                    time.sleep(0.2)
                    continue
                fails, self.error = 0, None
                with self._lock:
                    self._frame, self._ts = f, time.time()
                time.sleep(1.0 / config.CAMERA_FPS)
            except Exception as e:
                self.error = str(e)
                self._release()
                time.sleep(2)

    def get(self):
        """-> frame copy or None if offline/stale (>3 s old)."""
        with self._lock:
            if self._frame is None or self._ts is None or time.time() - self._ts > 3.0:
                return None
            return self._frame.copy()


def synthetic_frame(w=640, h=360):
    img = np.full((h, w, 3), (30, 32, 36), np.uint8)
    cv2.rectangle(img, (0, h // 4), (w, 3 * h // 4), (52, 54, 60), -1)
    for x in range(0, w, 40):
        cv2.line(img, (x, h // 4), (x, 3 * h // 4), (45, 47, 52), 1)
    cv2.putText(img, "SIMULATED FRAME (DEMO)", (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 180, 0), 2)
    return img
