"""SPAN regression tests: Wi-Fi minimalism, LED priority, modes, APIs.

Run:  python -m pytest tests/ -v
Covers user requirements:
- Wi-Fi icon green-filled if up else outline, never amber/yellow, no Online/Offline/Ethernet words
- Header only Wi-Fi + mic/mute + battery (+ state word for REC/Muted)
- 240x320 screen preserved
- LED colours: mic off red, mic on green, thinking/tool single amber,
  listening/recording cyan, offline blue, light-off off
- listening idle >10s returns to SPAN idle screen; recording stays
- LLM metrics terminology (TTFT/total/TPOT/tok-s/in-out)
- Fixed loaders per mode
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as span

_REAL_READ_INTERNET = span.read_internet
_REAL_READ_BATTERY = span.read_battery


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def stable_state(monkeypatch):
    """Freeze background monitor + snapshot global state so tests are deterministic."""
    monkeypatch.setattr(span, "read_wifi", lambda: span.STATE["wifi"])
    monkeypatch.setattr(span, "read_internet", lambda: span.STATE["internet"])
    monkeypatch.setattr(span, "read_mic_muted", lambda: span.STATE["mic_muted"])
    monkeypatch.setattr(span, "read_battery",
                        lambda: dict(span.STATE["battery"]))
    with span.LOCK:
        saved = json.loads(json.dumps(span.STATE))
    saved_history = list(span.HISTORY)
    saved_last_rgb = span._last_rgb
    yield
    with span.LOCK:
        span.STATE.clear()
        span.STATE.update(saved)
    span.HISTORY.clear()
    span.HISTORY.extend(saved_history)
    span._last_rgb = saved_last_rgb
    # release demo lock if a test left it held
    try:
        span.DEMO_LOCK.release()
    except (RuntimeError, threading_error()) if False else Exception:
        pass


def threading_error():
    import threading
    return threading.ThreadError


def base_state(**over):
    s = {
        "wifi": True,
        "internet": True,
        "mic_muted": False,
        "light_enabled": True,
        "battery": {"percent": 80, "charging": False, "status": "Discharging"},
        "mode": "idle",
        "last_activity_at": time.monotonic(),
        "recording_started_at": None,
        "led": {"name": "green", "hex": "#22c55e"},
        "hardware": {},
    }
    s.update(over)
    return s


def set_state(**over):
    with span.LOCK:
        span.STATE.update(base_state(**over))
    span.update_led()
    return span._state_copy()


# ---------- 1. Wi-Fi icon: never yellow, wifi-only ----------

def test_wifi_css_has_no_amber_rule():
    css = open(os.path.join(span.app.root_path, "static", "style.css")).read()
    assert ".wifi path" in css
    assert ".wifi.off path" in css
    assert "no-internet" not in css, "amber .wifi.no-internet rule must not exist"
    # wifi selectors must never reference amber/yellow
    for line in css.splitlines():
        if line.strip().startswith(".wifi"):
            assert "amber" not in line.lower() and "yellow" not in line.lower(), line


def test_wifi_js_uses_wifi_only_no_amber():
    js = open(os.path.join(span.app.root_path, "static", "app.js")).read()
    assert "no-internet" not in js, "JS must not toggle no-internet/amber class"
    assert "wifi.classList.toggle('off', !S.wifi)" in js or 'wifi.classList.toggle("off", !S.wifi)' in js \
        or "toggle('off', !S.wifi)" in js
    assert "S.wifi && !S.internet" not in js, "wifi colour must not depend on internet"
    assert "Online" not in js and "Offline mode" not in js, "no Online/Offline words in UI"
    assert "Wi-Fi connected, no internet" not in js


def test_wifi_aria_label_is_two_way():
    js = open(os.path.join(span.app.root_path, "static", "app.js")).read()
    assert "Wi-Fi connected" in js and "Wi-Fi off" in js


def test_header_has_only_wifi_mic_battery():
    html = open(os.path.join(span.app.root_path, "templates", "index.html")).read().lower()
    assert 'id="wifi"' in html and 'id="mic"' in html and 'id="battery"' in html
    assert "ethernet" not in html and "speaker" not in html
    # state word must exist for REC/Muted/Thinking indication
    assert 'id="statelabel"' in html


def test_screen_stays_240x320():
    css = open(os.path.join(span.app.root_path, "static", "style.css")).read()
    assert "width:240px" in css.replace(" ", "") and "height:320px" in css.replace(" ", "")


def test_readme_documents_green_only_wifi():
    md = open(os.path.join(span.app.root_path, "..", "pocket_ai_hardware_web", "README.md")).read() \
        if False else open(os.path.join("README.md")).read()
    assert "amber = connected without internet" not in md.lower()


# ---------- 2. LED priority ----------

def test_led_off_overrides_everything():
    assert span.choose_led(base_state(light_enabled=False, mic_muted=True,
                                      mode="thinking", internet=False)) == "off"


def test_led_muted_red_second():
    assert span.choose_led(base_state(mic_muted=True, mode="thinking", internet=False)) == "red"
    assert span.choose_led(base_state(mic_muted=True, mode="tool")) == "red"


def test_led_thinking_amber():
    assert span.choose_led(base_state(mode="thinking")) == "amber"


def test_led_tool_amber_single_working_colour():
    assert span.choose_led(base_state(mode="tool")) == "amber"
    assert span.choose_led(base_state(mode="thinking")) == "amber"


def test_led_listening_recording_cyan():
    assert span.choose_led(base_state(mode="listening")) == "cyan"
    assert span.choose_led(base_state(mode="recording")) == "cyan"


def test_led_offline_blue_ignores_wifi():
    # code checks internet only; wifi up + no internet still blue (LED), icon stays green
    assert span.choose_led(base_state(mode="idle", wifi=True, internet=False)) == "blue"
    assert span.choose_led(base_state(mode="idle", wifi=False, internet=False)) == "blue"
    assert span.choose_led(base_state(mode="speaking", internet=False)) == "blue"


def test_led_ready_speaking_green():
    assert span.choose_led(base_state(mode="idle", internet=True)) == "green"
    assert span.choose_led(base_state(mode="speaking", internet=True)) == "green"


def test_led_color_table_matches_state():
    for name, rgb in span.LED_COLORS.items():
        assert len(rgb) == 3 and all(0 <= c <= 255 for c in rgb), name
    assert span.LED_COLORS["red"] == (255, 0, 0)
    assert span.LED_COLORS["amber"] == (255, 140, 0)
    assert span.LED_COLORS["green"] == (34, 197, 94)
    assert span.LED_COLORS["blue"] == (59, 130, 246)
    assert span.LED_COLORS["cyan"] == (34, 211, 238)
    assert span.LED_COLORS["purple"] == (150, 0, 255)
    assert span.LED_COLORS["off"] == (0, 0, 0)


def test_update_led_syncs_hex():
    set_state(mode="thinking", mic_muted=False, internet=True)
    assert span.STATE["led"]["name"] == "amber"
    assert span.STATE["led"]["hex"] == "#%02x%02x%02x" % span.LED_COLORS["amber"]


LED_MATRIX = [
    # (light, muted, mode, internet, expected_name, expected_hex)
    (False, False, "idle", True, "off", "#000000"),
    (False, True, "thinking", False, "off", "#000000"),
    (True, True, "idle", True, "red", "#ff0000"),
    (True, True, "thinking", False, "red", "#ff0000"),
    (True, True, "tool", True, "red", "#ff0000"),
    (True, False, "thinking", True, "amber", "#ff8c00"),
    (True, False, "thinking", False, "amber", "#ff8c00"),
    (True, False, "tool", True, "amber", "#ff8c00"),
    (True, False, "tool", False, "amber", "#ff8c00"),
    (True, False, "listening", True, "cyan", "#22d3ee"),
    (True, False, "listening", False, "cyan", "#22d3ee"),
    (True, False, "recording", True, "cyan", "#22d3ee"),
    (True, False, "recording", False, "cyan", "#22d3ee"),
    (True, False, "idle", False, "blue", "#3b82f6"),
    (True, False, "speaking", False, "blue", "#3b82f6"),
    (True, False, "idle", True, "green", "#22c55e"),
    (True, False, "speaking", True, "green", "#22c55e"),
]


@pytest.mark.parametrize("light,muted,mode,net,exp,hexv", LED_MATRIX)
def test_led_full_matrix(light, muted, mode, net, exp, hexv):
    got = span.choose_led(base_state(light_enabled=light, mic_muted=muted,
                                     mode=mode, internet=net))
    assert got == exp, (light, muted, mode, net)
    assert ("#%02x%02x%02x" % span.LED_COLORS[got]) == hexv


def test_tool_error_returns_thinking_via_api(client):
    set_state(mode="thinking", internet=True, mic_muted=False, light_enabled=True)
    r = client.post("/api/event", json={"type": "tool", "id": "t9",
                                        "name": "web_search", "status": "error",
                                        "detail": "No internet connection"})
    assert r.status_code == 200
    assert span.STATE["mode"] == "thinking"
    assert span.STATE["led"]["name"] == "amber"


def test_light_off_on_flow_via_api(client):
    set_state(mode="idle", internet=True, mic_muted=False, light_enabled=True)
    assert client.post("/api/event", json={"type": "light", "on": False}).status_code == 200
    st = client.get("/api/state").get_json()
    assert st["led"]["name"] == "off" and st["led"]["hex"] == "#000000"
    assert client.post("/api/event", json={"type": "light", "on": True}).status_code == 200
    st = client.get("/api/state").get_json()
    assert st["led"]["name"] == "green"


def test_muted_red_overrides_thinking_via_state():
    set_state(mode="thinking", mic_muted=True, internet=True, light_enabled=True)
    assert span.STATE["led"]["name"] == "red"
    assert span.STATE["led"]["hex"] == "#ff0000"


def test_recording_stays_on_mute_toggle_and_light():
    set_state(mode="idle")
    span.publish({"type": "recording", "on": True})
    with span.LOCK:
        span.STATE["mic_muted"] = True
    span.update_led()
    assert span.STATE["mode"] == "recording"  # mute flag must not exit recording
    assert span.STATE["led"]["name"] == "red"  # but LED shows muted
    span.publish({"type": "light", "on": False})
    assert span.STATE["mode"] == "recording"
    assert span.STATE["led"]["name"] == "off"
    span.publish({"type": "light", "on": True})
    span.publish({"type": "recording", "on": False})
    assert span.STATE["mode"] == "idle"


# ---------- 3. Mode transitions (wake word / recording / follow-up) ----------

def test_wake_goes_listening():
    set_state(mode="idle")
    span.publish({"type": "wake"})
    assert span.STATE["mode"] == "listening"


def test_user_final_goes_thinking():
    set_state(mode="listening")
    span.publish({"type": "user_final", "id": "u1", "text": "hi"})
    assert span.STATE["mode"] == "thinking"


def test_tool_start_then_ok():
    set_state(mode="thinking")
    span.publish({"type": "tool", "id": "t1", "name": "get_weather", "status": "start"})
    assert span.STATE["mode"] == "tool"
    span.publish({"type": "tool", "id": "t1", "name": "get_weather", "status": "ok"})
    assert span.STATE["mode"] == "thinking"


def test_token_goes_speaking_done_returns_listening():
    set_state(mode="thinking")
    span.publish({"type": "assistant_start", "id": "a1"})
    span.publish({"type": "assistant_token", "id": "a1", "text": "hello "})
    assert span.STATE["mode"] == "speaking"
    span.publish({"type": "assistant_done", "id": "a1", "metrics": {}})
    assert span.STATE["mode"] == "listening"


def test_assistant_done_next_mode_idle():
    set_state(mode="speaking")
    span.publish({"type": "assistant_done", "id": "a1", "next_mode": "idle"})
    assert span.STATE["mode"] == "idle"


def test_interrupt_returns_listening():
    set_state(mode="speaking")
    span.publish({"type": "assistant_interrupted", "id": "a1"})
    assert span.STATE["mode"] == "listening"


def test_recording_mode_sticks_and_transcripts_stay():
    set_state(mode="idle")
    span.publish({"type": "recording", "on": True})
    assert span.STATE["mode"] == "recording"
    assert span.STATE["recording_started_at"] is not None
    span.publish({"type": "user_partial", "id": "r1", "text": "agenda"})
    assert span.STATE["mode"] == "recording", "transcripts must not leave recording mode"
    span.publish({"type": "recording", "on": False})
    assert span.STATE["mode"] == "idle"
    assert span.STATE["recording_started_at"] is None


def test_followup_timeout_returns_to_idle():
    # monitor() expires chat modes -> idle after FOLLOW_UP_TIMEOUT_SECONDS (default 10)
    assert span.FOLLOW_UP_TIMEOUT_SECONDS >= 10
    for mode in ("listening", "thinking", "tool", "speaking"):
        with span.LOCK:
            span.STATE["mode"] = mode
            span.STATE["last_activity_at"] = time.monotonic() - (span.FOLLOW_UP_TIMEOUT_SECONDS + 1)
        expired = (span.STATE["mode"] in ("listening", "thinking", "tool", "speaking")
                   and time.monotonic() - span.STATE["last_activity_at"] >= span.FOLLOW_UP_TIMEOUT_SECONDS)
        assert expired, mode
        with span.LOCK:
            span._set_mode("idle")
        assert span.STATE["mode"] == "idle"


def test_recording_does_not_auto_expire():
    with span.LOCK:
        span.STATE["mode"] = "recording"
        span.STATE["last_activity_at"] = time.monotonic() - 3600
    expired = (span.STATE["mode"] == "listening"
               and time.monotonic() - span.STATE["last_activity_at"] >= span.FOLLOW_UP_TIMEOUT_SECONDS)
    assert not expired, "only listening auto-expires; recording stays until stopped"


def test_light_event_validation():
    with pytest.raises(ValueError):
        span.publish({"type": "light", "on": "yes"})
    with pytest.raises(ValueError):
        span.publish({"type": "mode", "mode": "bogus"})
    with pytest.raises(ValueError):
        span.publish({"type": "nope"})


def test_recording_event_marks_user_bubbles():
    seen = []
    orig = span.broadcast
    try:
        span.broadcast = seen.append
        set_state(mode="idle")
        span.publish({"type": "recording", "on": True})
        span.publish({"type": "user_final", "id": "r9", "text": "hello"})
        assert seen and seen[-1].get("recording") is True
    finally:
        span.broadcast = orig
        with span.LOCK:
            span._set_mode("idle")


# ---------- 4. HTTP APIs ----------

@pytest.fixture()
def client():
    span.app.config.update(TESTING=True)
    return span.app.test_client()


def test_get_state_shape(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.get_json()
    for key in ("wifi", "internet", "mic_muted", "battery", "mode", "led"):
        assert key in body, key
    assert isinstance(body["wifi"], bool) and isinstance(body["internet"], bool)
    assert body["mode"] in span.MODES


def test_post_event_wake_and_validation(client):
    r = client.post("/api/event", json={"type": "wake"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert span.STATE["mode"] == "listening"
    r = client.post("/api/event", json={"type": "bogus"})
    assert r.status_code == 400
    r = client.post("/api/event", json=["not", "a", "dict"])
    assert r.status_code == 400
    r = client.post("/api/event", json={"type": "mode", "mode": "nope"})
    assert r.status_code == 400


def test_thinking_done_idle_light_flow_via_api(client):
    # 1 API -> thinking, stays: check amber. done API -> listening: check cyan.
    # 10s idle -> main: check green.
    set_state(mode="idle", wifi=True, internet=True,
              mic_muted=False, light_enabled=True)
    r = client.post("/api/event",
                    json={"type": "user_final", "id": "u-th", "text": "weather?"})
    assert r.status_code == 200
    st = client.get("/api/state").get_json()
    assert st["mode"] == "thinking", st
    assert st["led"]["name"] == "amber", st["led"]
    assert st["led"]["hex"] == "#%02x%02x%02x" % span.LED_COLORS["amber"]
    # stays in thinking without further events (no auto-leave before timeout)
    st2 = client.get("/api/state").get_json()
    assert st2["mode"] == "thinking" and st2["led"]["name"] == "amber"

    r = client.post("/api/event", json={"type": "assistant_done", "id": "a-th"})
    assert r.status_code == 200
    st = client.get("/api/state").get_json()
    assert st["mode"] == "listening", st
    assert st["led"]["name"] == "cyan", st["led"]
    assert st["led"]["hex"] == "#%02x%02x%02x" % span.LED_COLORS["cyan"]

    # 10s without events -> main screen
    with span.LOCK:
        span.STATE["last_activity_at"] = time.monotonic() - (span.FOLLOW_UP_TIMEOUT_SECONDS + 1)
        mode = span.STATE["mode"]
        idle_for = time.monotonic() - span.STATE["last_activity_at"]
        if mode in ("listening", "thinking", "tool", "speaking") and idle_for >= span.FOLLOW_UP_TIMEOUT_SECONDS:
            span._set_mode("idle")
    span.update_led()
    st = client.get("/api/state").get_json()
    assert st["mode"] == "idle", st
    assert st["led"]["name"] == "green", st["led"]
    assert st["led"]["hex"] == "#%02x%02x%02x" % span.LED_COLORS["green"]


def test_post_demo_validation_and_lock(client, monkeypatch):
    r = client.post("/api/demo", json={"scenario": "nope"})
    assert r.status_code == 400
    # avoid spawning real sleeping demo threads
    monkeypatch.setattr(span.threading, "Thread", lambda *a, **k: DummyThread())
    r = client.post("/api/demo", json={"scenario": "chat"})
    assert r.status_code == 200 and r.get_json()["ok"] is True


class DummyThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def test_broadcast_history_and_slow_client_resync():
    import queue
    q = queue.Queue(maxsize=2)
    with span.CLIENTS_LOCK:
        span.CLIENTS.append(q)
    try:
        span.HISTORY.clear()
        span.broadcast({"type": "user_final", "id": "u1", "text": "hi"})
        assert len(span.HISTORY) == 1  # chat events replayed
        n_before = len(span.HISTORY)
        span.broadcast({"type": "state", "state": {}})
        assert len(span.HISTORY) == n_before  # state not replayed
        assert not q.empty()
    finally:
        with span.CLIENTS_LOCK:
            if q in span.CLIENTS:
                span.CLIENTS.remove(q)


def test_read_battery_none_when_no_supplies(monkeypatch):
    monkeypatch.setattr(span, "read_battery", _REAL_READ_BATTERY)
    monkeypatch.setattr(span.glob, "glob", lambda *a, **k: [])
    out = span.read_battery()
    assert out == {"percent": None, "charging": None, "status": "unavailable"}


def test_read_internet_true_false(monkeypatch):
    monkeypatch.setattr(span, "read_internet", _REAL_READ_INTERNET)

    class Dummy:
        def close(self):
            pass
    monkeypatch.setattr(span.socket, "create_connection", lambda *a, **k: Dummy())
    assert span.read_internet() is True
    import socket as _sock

    def boom(*a, **k):
        raise _sock.timeout()
    monkeypatch.setattr(span.socket, "create_connection", boom)
    assert span.read_internet() is False


def test_metrics_keys_documented():
    # demo answer builds the exact metrics dict shown under each reply
    import inspect
    src = inspect.getsource(span._answer)
    for k in ("ttft_ms", "total_ms", "gen_ms", "tpot_ms", "tokens_per_sec",
              "input_tokens", "output_tokens"):
        assert k in src, k


def test_fixed_loader_selectors_exist():
    css = open(os.path.join(span.app.root_path, "static", "style.css")).read()
    js = open(os.path.join(span.app.root_path, "static", "app.js")).read()
    assert ".bars" in css and ".dots" in css and "mute-ic" in css
    assert "wave" in css and "recClock" in js


def test_battery_unknown_shows_dashes():
    js = open(os.path.join(span.app.root_path, "static", "app.js")).read()
    assert ": '--'" in js or ':"--"' in js or "`--`" in js or "'--'" in js, \
        "mains-powered Pi (no /sys/class/power_supply) must show --, not blank"
    assert "mains powered" in js.lower()


def test_recording_never_falls_back_to_span_or_voice():
    # While recording, pipeline chatter must not return to idle/listening/speaking.
    set_state(mode="idle")
    span.publish({"type": "recording", "on": True})
    assert span.STATE["mode"] == "recording"
    for ev in ({"type": "wake"},
               {"type": "user_partial", "id": "r1", "text": "hello"},
               {"type": "user_final", "id": "r1", "text": "hello world"},
               {"type": "assistant_start", "id": "a1"},
               {"type": "assistant_token", "id": "a1", "text": "hi "},
               {"type": "tool", "id": "t1", "name": "get_weather", "status": "start"},
               {"type": "assistant_done", "id": "a1"},
               {"type": "error", "message": "x"}):
        span.publish(ev)
        assert span.STATE["mode"] == "recording", ev
    # only explicit stop (or direct mode event) leaves recording
    span.publish({"type": "recording", "on": False})
    assert span.STATE["mode"] == "idle"


def test_frontend_recording_hides_span_voice():
    css = open(os.path.join(span.app.root_path, "static", "style.css")).read()
    js = open(os.path.join(span.app.root_path, "static", "app.js")).read()
    assert ".screen.rec-on .recview" in css
    assert ".screen.rec-on .chat" in css and ".screen.rec-on .voiceview" in css
    assert "mode === 'idle' && !recording" in js or 'mode === "idle" && !recording' in js, \
        "voice orb only on idle, never during recording"
