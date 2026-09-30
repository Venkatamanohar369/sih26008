import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from telemetry import parse_line, normalize
from degradation import DegradationScorer, condition_of


def test_parse_errors():
    assert parse_line("")[1] == "empty"
    assert parse_line("boot msg")[1] == "non-JSON line"
    assert "malformed" in parse_line('{"a":')[1]
    assert parse_line('{"a":1}')[0] == {"a": 1}


def test_missing_and_fault_never_look_normal():
    t = normalize({"pass_count": 3, "sound": 420, "speed_rpm": 28}, "LIVE")
    assert t["temp_c"] is None and t["status"]["temp"] == "N/A"
    t = normalize({"temp_c": float("nan"), "sound": 99999, "speed_rpm": 5}, "LIVE")
    assert t["status"]["temp"] == "FAULT" and t["status"]["sound"] == "FAULT"
    t = normalize({"temp_c": 30, "status": {"temp": "FAULT"}}, "LIVE")
    assert t["temp_c"] is None and t["status"]["temp"] == "FAULT"


def test_score_progression_and_trend():
    s = DegradationScorer()
    for p in range(1, 6):
        r = s.update(p, "healthy_splice", 0.9, 30, 400)
    assert r["condition"] == "NORMAL"
    for p in range(6, 14):
        r = s.update(p, "belt_damage", 0.9, 30, 400)
    assert r["condition"] == "CRITICAL" and r["trend"] == "INCREASING"


def test_failed_sensor_adds_nothing():
    s = DegradationScorer()
    assert s.instant("healthy_splice", 0.9, None, None) == 0.0
    assert condition_of(0) == "NORMAL"
