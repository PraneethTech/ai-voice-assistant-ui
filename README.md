# SPAN — browser replica of the 240x320 device screen

A Flask app that serves three static files (HTML, CSS, vanilla JS). No Node, no build step, no
frameworks, no external fonts or CDNs, so it works offline. Chromium on the Pi (or any phone/laptop on
the same network) renders the 240x320 screen scaled to fit, inside a device frame.

## Run

```bash
source /path/to/aivenv/bin/activate
python -m pip install -r requirements.txt
python app.py                # PORT=5055 python app.py to change the port
```

- Device frame view: `http://<PI-IP>:5000/`
- Bare screen for the Pi's own display (Chromium kiosk): `http://127.0.0.1:5000/?kiosk=1`
- `?scale=1` renders at true 1x size. `?demo=1` plays the chat demo on load.
- Keys `1` `2` `3` `4` play the demos: chat, recording, offline, interrupt.

Stop any old TFT script first so it doesn't compete for GPIO 5 or the WS2812 (`pgrep -af final.py`).
Run as your desktop user, not with sudo, so `wpctl` reaches your PipeWire session.

## Screen

- Header: state on the left (Ready / Listening / ● REC 00:42 / Thinking / Using tool / Responding / Muted).
  Icons on the right: LED colour dot, Wi-Fi (filled green = connected, empty outline = not), mic (green,
  or red with a slash when muted; tap to toggle if no hardware switch), battery %.
- Body, wake-word conversation: full-height chat. Under each reply:
  `TTFT · total · tok/s` and `in · out · avg ms/tok` (hover for the full names).
- Body, recording mode: big waveform, timer and live transcript card. When recording stops, the
  transcript is kept in the chat as a REC bubble.
- Fixed loader row at the bottom: waveform (listening / responding), dots (thinking / tool), mic-off (muted).

## Hardware

| Part | How it's used |
|---|---|
| Latching push switch, GPIO 5 | Switch position sets mic mute (`SWITCH_PRESSED_MUTES` in app.py). Synced on startup. |
| USB mic (default source) | `wpctl set-mute @DEFAULT_AUDIO_SOURCE@` |
| MAX98357A speaker | Not shown on screen. |
| WS2812, board.D13 | One pixel, brightness 0.3, written only when the colour changes. |
| Battery | Read from `/sys/class/power_supply/*` when a UPS HAT driver exposes one; otherwise shows `--`. A bare Pi 5 has no battery. |

One background thread polls Wi-Fi (3s), internet (5s), mic mute (2s) and battery (15s), and pushes
only changes to the browser over Server-Sent Events. The browser does no polling.

LED priority (highest first):

| Condition | Colour |
|---|---|
| Mic muted | red |
| Thinking | amber |
| Using a tool | purple |
| Responding | green |
| Listening / recording | cyan |
| Idle, internet up | green |
| Idle, no internet | blue |

## Pipeline integration

Your wake word / VAD / STT / SLM / TTS code reports progress with `POST /api/event` (JSON), or, in the
same process, `from app import publish; publish({...})`. The screen mode follows the events.

```bash
E='curl -s -XPOST -H Content-Type:application/json localhost:5000/api/event -d'
$E '{"type":"wake"}'                                              # -> listening
$E '{"type":"user_partial","id":"u1","text":"what is the"}'
$E '{"type":"user_final","id":"u1","text":"What is the weather?"}' # -> thinking
$E '{"type":"tool","id":"t1","name":"get_weather","status":"start"}'                # -> tool
$E '{"type":"tool","id":"t1","name":"get_weather","status":"ok","detail":"31°C"}'   # -> thinking
$E '{"type":"assistant_start","id":"a1"}'
$E '{"type":"assistant_token","id":"a1","text":"It is "}'          # -> responding
$E '{"type":"assistant_done","id":"a1","metrics":{"ttft_ms":420,"total_ms":1900,"gen_ms":1480,"tpot_ms":61.7,"tokens_per_sec":16.2,"input_tokens":312,"output_tokens":24}}'
$E '{"type":"assistant_interrupted","id":"a1"}'                    # user barged in -> listening
$E '{"type":"recording","on":true}'                                # push switch -> recording view
$E '{"type":"recording","on":false}'
$E '{"type":"mode","mode":"idle"}'                                 # set mode directly
$E '{"type":"error","message":"STT failed"}'
```

`assistant_done` accepts `"next_mode":"listening"` to keep the conversation open (default `idle`).
While recording, `user_partial` / `user_final` go to the recording card instead of the chat.
Ids only need to be unique among bubbles still in progress, so reusing `u1`/`a1` every turn is fine.

Metric names: TTFT = time to first token; total = end-to-end latency (request → last token);
gen = decode time (first → last token); TPOT ("avg") = average time per output token;
tok/s = output tokens per second; input/output = prompt and completion token counts.

Recent events are replayed to a browser that connects or reloads mid-conversation.
Keep this server on a trusted network; it has no authentication.
