# SIH26008 — Intelligent Conveyor Belt Health Monitoring (Streamlit HMI)

**Implemented (MVP):** visible-defect detection (YOLOv8n, 3 classes), per-pass tracking via IR pass count, progressive
degradation score + trend, NORMAL/WARNING/CRITICAL, telemetry-heartbeat monitoring, SQLite logging.
**Not implemented / not claimed:** rupture prediction, failure dates, RUL, industrial thresholds, certified safety.
All thresholds live in `config.py` and are prototype defaults that you must calibrate.

## Architecture / data flow
```
USB webcam -> camera.py (thread, 5 fps) -+
                                         +-> engine.py (1 background thread per process)
                  yolo_detector.py <-----+     ~1 inference/s, worst detection aggregated per belt pass
ESP32 --serial JSON--> telemetry.py -----+     new pass_count => degradation.py score -> trend -> condition
                                               -> database.py (SQLite) ; CRITICAL => safety.py stop latch
app.py (Streamlit) --reads snapshot only--> browser (0.0.0.0:8501) --> Cloudflare Tunnel --> HTTPS
```
DETECT -> LOCALIZE -> TRACK -> ASSESS -> PREDICT TREND -> ACT. The UI never opens serial/camera itself, so multiple
browser tabs don't fight over `/dev/ttyUSB0`. DEMO and LIVE rows are tagged `source` in every table and never mixed.

## Scoring (degradation.py)
`instant = class_weight * confidence (+5/+10 for temp/sound over warn/crit)`, capped at 100;
`score = EMA(instant)` across passes (decays on healthy passes). Trend = least-squares slope over the last N passes
(`INCREASING / STABLE / DECREASING / INSUFFICIENT DATA`). A failed sensor adds **nothing** and is shown FAULT/N/A.
A pass with no valid vision frame (camera/YOLO fault) is **not scored** and logged in `system_events`.
"Belt position" = bounding-box X as % of frame width (no belt-length encoder exists).

## ESP32 -> Pi JSON contract (one line per message, `\n` terminated, 115200 baud)
```json
{"ts":1727600000,"temp_c":31.2,"sound":420,"speed_rpm":28,"pass_count":12,
 "status":{"temp":"OK","sound":"OK","speed":"OK"},
 "motor":"RUNNING","safety_switch":"ENABLED"}
```
`motor` (RUNNING|STOPPED) and `safety_switch` (ENABLED|DISABLED) are optional; if absent the dashboard shows
"inferred"/"UNKNOWN". Missing field => `N/A`. NaN/out-of-range/`"FAULT"` => `FAULT`. Use `null` (not 0) for a dead sensor.
Optional Pi->ESP32 line when `ENABLE_STOP_COMMAND=1`: `{"cmd":"MOTOR_STOP"}` — your firmware must implement it AND
independently stop the motor if serial goes silent. The dashboard cannot guarantee that.

## Windows development (DEMO mode, no hardware)
```powershell
cd sih26008
py -m venv .venv ; .\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py
```
Open http://localhost:8501 (DEMO is default; ~1 min to see NORMAL -> WARNING -> CRITICAL). LIVE with a real ESP32 on
Windows: set `SERIAL_PORT=COM3` in `.env`, `pip install -r requirements-ai.txt`, put the model in `models/`.

## Raspberry Pi 4B deployment (64-bit Raspberry Pi OS)
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip libatlas-base-dev
sudo usermod -aG dialout $USER && sudo reboot          # serial permission
cd ~/sih26008 && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ai.txt
cp .env.example .env && nano .env                       # APP_MODE=LIVE, SERIAL_PORT=/dev/ttyUSB0 (or ttyACM0), ADMIN_PASSWORD=...
```
Model: on any PC `yolo export model=best.pt format=ncnn imgsz=320`, then copy the folder `best_ncnn_model/` to
`models/` (or copy `best.pt` as fallback). Class order must be: healthy_splice, splice_anomaly, belt_damage.

**Start command**
```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```
Optional autostart `/etc/systemd/system/beltdash.service`:
```ini
[Unit]
Description=SIH26008 dashboard
After=network-online.target
[Service]
User=pi
WorkingDirectory=/home/pi/sih26008
ExecStart=/home/pi/sih26008/.venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
Restart=always
[Install]
WantedBy=multi-user.target
```
`sudo systemctl daemon-reload && sudo systemctl enable --now beltdash`

## Cloudflare Tunnel (separate from the app)
```bash
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
# A) Quick temporary URL (no domain needed; URL changes each run):
cloudflared tunnel --url http://localhost:8501
# B) Permanent (needs a domain on Cloudflare):
cloudflared tunnel login
cloudflared tunnel create sih26008
cloudflared tunnel route dns sih26008 dash.yourdomain.com
mkdir -p ~/.cloudflared && nano ~/.cloudflared/config.yml
```
```yaml
tunnel: sih26008
credentials-file: /home/pi/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: dash.yourdomain.com
    service: http://localhost:8501
  - service: http_status:404
```
```bash
cloudflared tunnel run sih26008
sudo cloudflared service install        # autostart
```
Security: only port 8501 is tunnelled; serial/motor are never exposed as APIs. Set `ADMIN_PASSWORD` (locks mode
switch/reset/ack) and preferably add a Cloudflare Access policy. If the page loads but never updates, add
`--server.enableCORS false --server.enableXsrfProtection false` only as a last resort.

## Testing procedure
1. `pip install pytest && pytest -q tests` (parsing, fault handling, scoring/trend).
2. DEMO: start app; confirm DEMO banner, NORMAL->WARNING->CRITICAL, "MOTOR STOP COMMAND" (advisory), timeline moves.
   Tick "inject temperature sensor fault": Temperature card must show **FAULT** and a gap in sensor history.
3. LIVE without hardware: switch to LIVE => ESP32 OFFLINE, Serial DISCONNECTED, Camera OFFLINE, "COMMUNICATION FAULT".
4. LIVE serial: `cat /dev/ttyUSB0` shows JSON lines; ESP32 ONLINE, heartbeat HEALTHY. Unplug ESP32 => STALE after 3 s,
   COMMUNICATION FAULT after 5 s, auto-reconnect on replug. Send garbage lines => "serial_malformed" rises, app survives.
5. Unplug camera => CAMERA OFFLINE, passes are logged "NOT scored". Rename model => YOLO ERROR shown.
6. Kill Wi-Fi: dashboard keeps logging locally (SQLite). Check `data/belt_monitor.db` and `logs/app.log`.
7. Remote: open the tunnel URL from a phone on mobile data; confirm read-only without the password.

## Layout
`app.py config.py engine.py database.py camera.py yolo_detector.py telemetry.py degradation.py safety.py`
`models/ data/ logs/ tests/ requirements.txt requirements-ai.txt .env.example`
