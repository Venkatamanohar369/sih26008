"""SIH26008 dashboard (Streamlit). Run: streamlit run app.py --server.address 0.0.0.0 --server.port 8501"""
import hmac, os, platform, socket, json, datetime as dt
import cv2, pandas as pd, plotly.graph_objects as go, streamlit as st
from plotly.subplots import make_subplots
import config
from engine import Engine

st.set_page_config(page_title=config.APP_TITLE, page_icon="🏭", layout="wide", initial_sidebar_state="expanded")


@st.cache_resource(show_spinner=False)
def get_engine():
    return Engine()


eng = get_engine()

COL = {"ok": "#3fb950", "warn": "#d29922", "crit": "#f85149", "na": "#8b949e", "sim": "#58a6ff"}
st.markdown("""<style>
.stApp{background:#0b0f14;color:#d6dde6}
header[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1rem;max-width:1600px}
[data-testid="stSidebar"]{background:#0e141b;border-right:1px solid #1f2933}
h1,h2,h3,h4,h5,h6{color:#e6edf3;letter-spacing:.05em;text-transform:uppercase;font-size:.85rem!important}
.hdr{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;
 border:1px solid #1f2933;border-left:4px solid #58a6ff;background:#111820;padding:10px 16px;border-radius:3px}
.hdr .t{font-size:1.15rem;font-weight:700;color:#e6edf3;letter-spacing:.03em}
.chip{display:inline-block;border:1px solid #2b3642;background:#0f151c;padding:3px 9px;margin:2px;border-radius:3px;font-size:.75rem}
.chip b{margin-left:6px}
.card{border:1px solid #1f2933;background:#111820;border-radius:3px;padding:10px 12px;height:100%;min-height:92px}
.card .lbl{font-size:.68rem;color:#8b949e;letter-spacing:.08em;text-transform:uppercase}
.card .val{font-size:1.5rem;font-weight:700;line-height:1.3}
.card .sub{font-size:.7rem;color:#8b949e}
.pipe span{display:inline-block;padding:4px 12px;background:#131b24;border:1px solid #2b3642;font-size:.72rem;letter-spacing:.1em;color:#9fb1c2}
.pipe i{color:#58a6ff;font-style:normal;margin:0 4px}
.banner{padding:8px 14px;border-radius:3px;font-weight:700;letter-spacing:.06em;margin:6px 0}
.stage{display:inline-block;padding:6px 16px;border:1px solid #2b3642;margin-right:2px;font-size:.78rem;letter-spacing:.08em}
.small{font-size:.72rem;color:#8b949e}
</style>""", unsafe_allow_html=True)

OK_W = {"ONLINE", "READY", "CONNECTED", "RUNNING", "HEALTHY", "OK", "NORMAL", "ENABLED", "NONE", "STABLE", "DECREASING"}
CRIT_W = {"OFFLINE", "ERROR", "DISCONNECTED", "STOPPED", "TIMEOUT", "FAULT", "CRITICAL", "NO", "DISABLED", "STOP", "HIGH", "INCREASING"}
WARN_W = {"WARNING", "STALE", "UNKNOWN", "MEDIUM", "INSUFFICIENT"}


def lvl(text):
    w = str(text).upper().replace("(", " ").split()[0] if str(text).strip() else "N/A"
    if w == "SIMULATED" or w == "DEMO":
        return "sim"
    return "ok" if w in OK_W else "crit" if w in CRIT_W else "warn" if w in WARN_W else "na"


def chip(label, value, level=None):
    c = COL[level or lvl(value)]
    return f'<span class="chip">{label}<b style="color:{c}">{value}</b></span>'


def card(label, value, sub="", level="na"):
    return (f'<div class="card"><div class="lbl">{label}</div>'
            f'<div class="val" style="color:{COL[level]}">{value}</div><div class="sub">{sub}</div></div>')


def banner(text, level, sub=""):
    c = COL[level]
    return (f'<div class="banner" style="border:1px solid {c};color:{c};background:{c}22">{text}'
            f'<span class="small" style="margin-left:12px;font-weight:400">{sub}</span></div>')


# ---------------- sidebar (controls) ----------------
S0 = eng.get_state()
with st.sidebar:
    st.markdown("### Control")
    admin = True
    if config.ADMIN_PASSWORD:
        pw = st.text_input("Admin password", type="password", help="Required to change mode / reset / acknowledge.")
        admin = hmac.compare_digest(pw.encode(), config.ADMIN_PASSWORD.encode())
        if not admin:
            st.caption("🔒 Read-only view")
    mode = st.radio("Data mode", ["DEMO", "LIVE"], index=0 if eng.mode == "DEMO" else 1, disabled=not admin,
                    help="DEMO = simulated telemetry/frames. LIVE = real ESP32 serial + USB camera + YOLO.")
    if admin and mode != eng.mode:
        eng.set_mode(mode)
        st.rerun()
    refresh = st.slider("Refresh interval (s)", 1, 10, config.UI_REFRESH_S)
    if eng.mode == "DEMO":
        eng.demo.inject_fault = st.checkbox("Demo: inject temperature sensor fault", value=eng.demo.inject_fault, disabled=not admin)
        if st.button("Reset demo history", disabled=not admin):
            eng.reset_demo(); st.rerun()
    if st.button("Acknowledge motor-stop latch", disabled=not admin):
        eng.ack_stop(); st.rerun()

    st.markdown("### Status (at last interaction)")
    st.markdown(chip("Camera", S0["camera"]) + chip("Model", S0["yolo"]) + chip("Serial", S0["serial"]) +
                chip("DB", S0["db"]), unsafe_allow_html=True)

    st.markdown("### Threshold configuration")
    st.caption("Prototype defaults - NOT industrial standards. Edit in config.py.")
    with st.expander("View thresholds"):
        st.json({**config.THRESHOLDS, "class_weights": config.CLASS_WEIGHTS})

    st.markdown("### System information")
    cpu = "n/a"
    try:
        cpu = f"{int(open('/sys/class/thermal/thermal_zone0/temp').read()) / 1000:.1f} °C"
    except Exception:
        pass
    st.caption(f"Host: {socket.gethostname()}  \nPlatform: {platform.system()} {platform.machine()}  \n"
               f"Python: {platform.python_version()}  \nPi CPU temp: {cpu}  \n"
               f"Stop command tx: {'ENABLED' if config.ENABLE_STOP_COMMAND else 'disabled'}")

# ---------------- render helpers ----------------
def fmt(tel, key, unit, digits=1):
    if tel is None:
        return "N/A", "na"
    st_key = {"temp_c": "temp", "sound": "sound", "speed_rpm": "speed"}[key]
    s = tel["status"][st_key]
    if s != "OK":
        return s, "crit" if s == "FAULT" else "na"
    return f"{tel[key]:.{digits}f}{unit}", "ok"


def header(S):
    tag = ("SIMULATED DATA - DEMO MODE", "sim") if S["mode"] == "DEMO" else ("LIVE", "ok")
    pi = "ONLINE"
    esp = S["esp32"]
    st.markdown(f"""<div class="hdr"><div class="t">{config.APP_TITLE}</div>
    <div>{chip('●', tag[0], tag[1])}{chip('TIME', dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'na')}
    {chip('Raspberry Pi (dashboard host)', pi)}{chip('ESP32', esp)}{chip('Motor', S['motor'])}</div></div>""",
                unsafe_allow_html=True)
    st.markdown('<div class="pipe" style="margin:8px 0">' + '<i>→</i>'.join(
        f"<span>{x}</span>" for x in ["DETECT", "LOCALIZE", "TRACK", "ASSESS", "PREDICT TREND", "ACT"]) + '</div>',
                unsafe_allow_html=True)
    if S["mode"] == "DEMO":
        st.markdown(banner("DEMO MODE - all telemetry, frames and detections below are SIMULATED", "sim",
                           "No hardware is being read."), unsafe_allow_html=True)


def camera_and_health(S):
    c1, c2 = st.columns([2, 1])
    last = S["last"]
    with c1:
        st.markdown(f"##### Live camera — {S['frame_src'] or 'no source'}")
        if S["frame"] is not None:
            st.image(cv2.cvtColor(S["frame"], cv2.COLOR_BGR2RGB), use_container_width=True)
        else:
            st.markdown(banner("CAMERA OFFLINE / NO FRAME", "crit", S["det_err"] or ""), unsafe_allow_html=True)
        if S["det_err"] and S["frame"] is not None:
            st.markdown(banner("YOLO INFERENCE FAULT", "crit", S["det_err"]), unsafe_allow_html=True)
        pc = S["tel"]["pass_count"] if S["tel"] else None
        if S["dets"]:
            st.caption(" | ".join(f"{d['cls']} {d['conf']:.2f} @ {d['x_pct']:.0f}% frame-X · pass {pc if pc is not None else 'N/A'}"
                                  for d in S["dets"]))
        elif S["frame"] is not None:
            st.caption(f"No defect detected in latest frame · pass {pc if pc is not None else 'N/A'}")
    with c2:
        st.markdown("##### Belt health")
        if last:
            cond, score = last["condition"], last["score"]
            cl = {"NORMAL": "ok", "WARNING": "warn", "CRITICAL": "crit"}[cond]
            tl = {"INCREASING": "crit", "STABLE": "ok", "DECREASING": "ok"}.get(last["trend"], "na")
            arrow = {"INCREASING": "▲", "STABLE": "▬", "DECREASING": "▼"}.get(last["trend"], "…")
            st.markdown(card("Condition", cond, "prototype threshold, not a standard", cl), unsafe_allow_html=True)
            a, b = st.columns(2)
            a.markdown(card("Degradation score", f"{score:.1f}/100", "smoothed across passes", cl), unsafe_allow_html=True)
            b.markdown(card("Trend", f"{arrow} {last['trend']}", "", tl), unsafe_allow_html=True)
            a, b = st.columns(2)
            a.markdown(card("Current pass", last["pass"], "last scored", "na"), unsafe_allow_html=True)
            b.markdown(card("Detected defect", last["cls"], "", lvl(last["severity"])), unsafe_allow_html=True)
            if last.get("restored"):
                st.caption("Restored from database (previous session).")
        else:
            st.markdown(card("Condition", "AWAITING DATA", "waiting for first scored belt pass", "na"), unsafe_allow_html=True)


def sensor_cards(S):
    tel = S["tel"]
    ncols = st.columns(6)
    src = "SIMULATED" if S["mode"] == "DEMO" else "LIVE"
    if S["hb_fault"]:
        tel_view = None
        sub_extra = "NO TELEMETRY"
    else:
        tel_view, sub_extra = tel, ("STALE DATA" if S["stale"] else src)
    T = config.THRESHOLDS
    v, l = fmt(tel_view, "temp_c", " °C")
    if l == "ok":
        t = tel_view["temp_c"]; l = "crit" if t >= T["temp_crit_c"] else "warn" if t >= T["temp_warn_c"] else "ok"
    ncols[0].markdown(card("Temperature", v, sub_extra, "warn" if S["stale"] and l == "ok" else l), unsafe_allow_html=True)
    v, l = fmt(tel_view, "sound", "", 0)
    if l == "ok":
        t = tel_view["sound"]; l = "crit" if t >= T["sound_crit"] else "warn" if t >= T["sound_warn"] else "ok"
    ncols[1].markdown(card("Acoustic level (raw)", v, sub_extra, "warn" if S["stale"] and l == "ok" else l), unsafe_allow_html=True)
    v, l = fmt(tel_view, "speed_rpm", " rpm")
    ncols[2].markdown(card("Belt speed", v, sub_extra, "warn" if S["stale"] and l == "ok" else l), unsafe_allow_html=True)
    pc = tel_view["pass_count"] if tel_view else None
    ncols[3].markdown(card("Pass count", "N/A" if pc is None else pc, sub_extra, "na" if pc is None else "ok"), unsafe_allow_html=True)
    ncols[4].markdown(card("ESP32", S["esp32"], f"serial {S['serial']}", lvl(S["esp32"])), unsafe_allow_html=True)
    ncols[5].markdown(card("Pi", "ONLINE", "dashboard process running", "ok"), unsafe_allow_html=True)


def trend_chart(S):
    hist = S["hist"]
    fig = go.Figure()
    if hist:
        x, y = zip(*hist)
        fig.add_scatter(x=x, y=y, mode="lines+markers", name="score", line=dict(color="#58a6ff", width=2))
    T = config.THRESHOLDS
    fig.add_hline(y=T["warning_at"], line_dash="dash", line_color=COL["warn"], annotation_text="warning (config)")
    fig.add_hline(y=T["critical_at"], line_dash="dash", line_color=COL["crit"], annotation_text="critical (config)")
    tr = S["last"]["trend"] if S["last"] else "INSUFFICIENT DATA"
    fig.update_layout(template="plotly_dark", height=320, paper_bgcolor="#0b0f14", plot_bgcolor="#0f151c",
                      title=f"Progressive Degradation Trend — {tr}", xaxis_title="Belt pass",
                      yaxis_title="Degradation score", yaxis_range=[0, 100], margin=dict(l=10, r=10, t=50, b=10),
                      showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def localization_and_timeline(S):
    last = S["last"]
    st.markdown("##### Fault localization (last scored pass)")
    if last:
        conf = "N/A" if last["conf"] is None else f"{last['conf']:.2f}"
        rows = [("Defect class", last["cls"]), ("Belt position", last.get("position") or "N/A"),
                ("Pass number", last["pass"]), ("Confidence", conf), ("Severity", last["severity"])]
        st.table(pd.DataFrame(rows, columns=["Field", "Value"]).set_index("Field"))
        st.caption("Position = bounding-box X within the camera frame (no belt-length encoder in this prototype).")
    else:
        st.info("No scored pass yet.")
    st.markdown("##### Condition timeline")
    order = ["NORMAL", "ANOMALY", "WARNING", "CRITICAL"]
    cur = last["stage"] if last else None
    colors = {"NORMAL": "ok", "ANOMALY": "warn", "WARNING": "warn", "CRITICAL": "crit"}
    html = ""
    for i, s in enumerate(order):
        on = cur == s
        c = COL[colors[s]] if on else "#2b3642"
        html += (f'<span class="stage" style="border-color:{c};color:{c if on else "#6b7785"};'
                 f'background:{c + "22" if on else "transparent"}">{s}</span>' + ('<i style="color:#58a6ff">→</i>' if i < 3 else ""))
    st.markdown(html, unsafe_allow_html=True)


def safety_and_status(S):
    a, b = st.columns(2)
    with a:
        st.markdown("##### Safety panel")
        if S["hb_fault"]:
            st.markdown(banner("COMMUNICATION FAULT", "crit", f"heartbeat {S['hb']} — no valid telemetry from ESP32"), unsafe_allow_html=True)
        if S["stop_latched"]:
            st.markdown(banner("MOTOR STOP COMMAND", "crit", S["stop_note"]), unsafe_allow_html=True)
        age = "N/A" if S["hb_age"] is None else f"{S['hb_age']:.1f} s ago"
        if S["mode"] == "DEMO":
            age = "simulated"
        rows = [("Pi ↔ ESP32 heartbeat", S["hb"] + " (telemetry-based)"), ("Last heartbeat", age),
                ("Sensor health", ", ".join(f"{k}:{v}" for k, v in S["tel"]["status"].items()) if S["tel"] else "N/A"),
                ("Motor status", S["motor"]), ("Safety switch", S["switch"])]
        st.table(pd.DataFrame(rows, columns=["Item", "State"]).set_index("Item"))
        st.caption("Prototype logic only - NOT an industrial certified safety system. The physical motor safety "
                   "switch is the primary safety means. Heartbeat = arrival of valid telemetry.")
    with b:
        st.markdown("##### System status")
        st.markdown(
            chip("Camera", S["camera"]) + chip("YOLO", S["yolo"]) + chip("ESP32", S["esp32"]) +
            chip("Serial", S["serial"]) + chip("Database", S["db"]) + chip("Motor", S["motor"]) +
            chip("Heartbeat", S["hb"]), unsafe_allow_html=True)
        with st.expander("Technical details"):
            st.json({"mode": S["mode"], "serial_port": S["serial_port"], "serial_last_error": S["serial_err"],
                     "serial_rx_ok": S["rx_count"], "serial_malformed": S["malformed"],
                     "camera_error": S["camera_err"], "yolo_error": S["yolo_err"], "db_error": S["db_err"],
                     "telemetry_age_s": S["tel_age"], "frames_in_current_pass_window": S["window_frames"],
                     "last_pass_seen": S["last_pass_seen"],
                     "latest_normalized_telemetry": {k: v for k, v in (S["tel"] or {}).items()}}, expanded=False)


def render_live():
    S = eng.get_state()
    header(S)
    camera_and_health(S)
    st.markdown("##### Sensors")
    sensor_cards(S)
    c1, c2 = st.columns([3, 2])
    with c1:
        trend_chart(S)
    with c2:
        localization_and_timeline(S)
    safety_and_status(S)


def render_history():
    st.markdown("### Event log & historical data")
    n = st.radio("Rows", [50, 100], horizontal=True)
    src = eng.mode
    st.caption(f"Showing {src} data only (sources are never mixed).")
    t1, t2, t3, t4, t5 = st.tabs(["Event log", "Degradation trend", "Sensor history", "Condition history", "System events"])
    ev = eng.db.get_events(src, n)
    with t1:
        st.dataframe(ev.rename(columns={"detected_class": "class"}), use_container_width=True, hide_index=True)
    sc = eng.db.get_scores(src, n)
    with t2:
        if sc.empty:
            st.info("No history yet.")
        else:
            f = go.Figure(go.Scatter(x=sc["pass_count"], y=sc["degradation_score"], mode="lines+markers"))
            f.update_layout(template="plotly_dark", height=300, xaxis_title="Belt pass", yaxis_title="Score",
                            title="Progressive Degradation Trend (history)", yaxis_range=[0, 100])
            st.plotly_chart(f, use_container_width=True)
    with t3:
        tl = eng.db.get_telemetry(src, n).iloc[::-1]
        if tl.empty:
            st.info("No telemetry yet.")
        else:
            f = make_subplots(rows=3, cols=1, shared_xaxes=True, subplot_titles=("Temperature °C", "Acoustic (raw)", "Speed rpm"))
            for i, c in enumerate(["temp_c", "sound", "speed_rpm"], 1):
                f.add_scatter(x=tl["timestamp"], y=tl[c], mode="lines", row=i, col=1, connectgaps=False)
            f.update_layout(template="plotly_dark", height=520, showlegend=False)
            st.plotly_chart(f, use_container_width=True)
            st.caption("Gaps = sensor FAULT / N/A (never filled with normal-looking values).")
    with t4:
        if sc.empty:
            st.info("No history yet.")
        else:
            m = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}
            f = go.Figure(go.Scatter(x=sc["pass_count"], y=sc["condition"].map(m), mode="lines+markers", line_shape="hv"))
            f.update_layout(template="plotly_dark", height=280, xaxis_title="Belt pass",
                            yaxis=dict(tickvals=[0, 1, 2], ticktext=list(m)), title="Condition history")
            st.plotly_chart(f, use_container_width=True)
    with t5:
        st.dataframe(eng.db.get_system_events(n), use_container_width=True, hide_index=True)


def render_future():
    with st.expander("Future Industrial Expansion"):
        st.markdown("**NOT IMPLEMENTED in the current prototype** - roadmap only.")
        for x in ["MPU6050 vibration", "DS18B20 motor/roller temperature", "INMP441 digital acoustic sensing",
                  "Load cell + HX711", "Tension sensing", "Thermal camera", "Digital conveyor representation",
                  "PLC/SCADA integration"]:
            st.markdown(f"- {x} — `NOT IMPLEMENTED`")


st.fragment(run_every=refresh)(render_live)()
st.fragment(run_every=max(refresh * 3, 6))(render_history)()
render_future()
