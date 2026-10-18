# Desk Assistant

A small push-to-talk desk assistant for a Raspberry Pi 5. Four LED arcade buttons and the official 7-inch touchscreen send text to a Grok Bot webhook. The reply comes back as plain text on an [ntfy](https://ntfy.sh) topic, shows on the screen, and is spoken through a USB speakerphone.

The same program runs on a laptop. Keyboard keys stand in for the GPIO buttons, and `--test` answers with a local mock of the webhook so nothing leaves the machine.

## How a request moves

```
mic  -- record --> speech-to-text --\
GPIO preset -------------------------+--> HTTPS POST webhook
touchscreen preset or typed text ---/         |
                                               |  200 {"success": true, "runUuid": "..."}
                                               v
                                         assistant routine
                                               |
                                               |  plain text
                                               v
                                     https://ntfy.sh/<reply_topic>
                                               |
                                               v
                                    screen  +  offline TTS
```

The POST body is exactly:

```json
{"text": "Give me my morning briefing.", "source": "button", "reply_topic": "desk-…"}
```

`source` is `voice`, `button`, or `touch`. The header is `Authorization: Bearer <key>`.

A **200** with `{"success": true, "runUuid": "..."}` only means the routine accepted the request. The spoken answer arrives later on ntfy, which can take a few seconds or most of a minute. The screen stays on **Thinking** until that message arrives, and gives up after 3 minutes.

**400** means the routine is turned off. **401** means the key was rejected. Both show a specific message on the screen. Other failures say the desk could not reach the assistant.

The first ntfy message that arrives while the desk is waiting is the reply: it is shown and spoken. Anything that arrives while the desk is idle is a notification. Notifications are shown and, by default, not spoken.

## Parts

This is the hardware the software is set up for.

| Piece | Notes |
| --- | --- |
| Raspberry Pi 5, 8 GB | Raspberry Pi OS 64-bit **with desktop**, autologin on |
| Raspberry Pi Touch Display 2, 7 inch | Native 720×1280 portrait. Used rotated to 1280×720 landscape |
| SmartiPi Touch Pro 3 | Stand for the Pi 5 and the Touch Display 2 |
| Raspberry Pi 27 W USB-C power supply | |
| A2 microSD, 64 GB | |
| Raspberry Pi Active Cooler | |
| RTC battery | For the Pi 5 real-time clock |
| Anker PowerConf S330 | USB speakerphone. One device for the microphone and the speaker |
| Adafruit LED arcade buttons, 4 | In a separate box. Switches go to GPIO. LEDs are powered from 5 V and are not driven by code |
| Enclosure for the buttons | Long leads back to the Pi: 4 signal wires, GND, and 5 V |

## Flash the card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on another computer.

1. Install Imager and insert the 64 GB A2 microSD card.
2. Choose device **Raspberry Pi 5**.
3. Choose OS **Raspberry Pi OS (64-bit)**. That is the desktop image. Do not choose Lite. The desk UI needs the Wayland session.
4. Choose the microSD card as the storage.
5. When Imager offers customisation, set the hostname, username, password, wireless network, and locale. Turn on SSH if you want a remote shell.
6. Write the card and let Imager verify it.
7. Fit the Active Cooler, RTC battery, Touch Display 2, and SmartiPi stand. Boot from the new card with the 27 W supply.
8. On the first desktop session, turn on autologin so a graphical session exists at boot: `sudo raspi-config`, then System Options, Boot / Auto Login, Desktop Autologin. Rotate the panel to landscape before relying on the fullscreen UI. That is described under [Screen](#screen).

## Wiring

The four Adafruit LED arcade buttons live in their own box, with long leads back to the Pi header. Six wires are enough: four switch signals, one GND, and one 5 V.

BCM numbering. Each switch uses the internal pull-up, and a press connects that GPIO pin to ground. No resistor on the switch. The LED pair is a separate circuit. It is tied to 5 V through the resistor that comes with the button, and the program never switches it. The lamps are on whenever the Pi has 5 V.

The Touch Display 2 power cable already occupies pin 2 (5 V) and pin 6 (GND). Its plug may also cover pin 4. Leave that cable where it is. Take button ground from pin 9. Take LED 5 V from pin 4 when that pin is free. If the display plug covers pin 4, add a stacking header so pin 2 or pin 4 can feed the LED lead, or splice the LED 5 V lead onto the display cable's red wire. Do not connect 5 V to a GPIO pin.

```
Display power cable stays on pin 2 (5 V) and pin 6 (GND).

Button box, six long leads:

  pin 4  5V  ----+---- resistor ---- LED anode, talk
                 +---- resistor ---- LED anode, briefing
                 +---- resistor ---- LED anode, calendar
                 +---- resistor ---- LED anode, inbox

  pin 9  GND ----+---- all four LED cathodes
                 +---- all four switch commons

  pin 11 GPIO17  ---- talk switch
  pin 13 GPIO27  ---- briefing switch
  pin 15 GPIO22  ---- calendar switch
  pin 16 GPIO23  ---- inbox switch
```

| Control | BCM | Physical pin | Other leg | Laptop key |
| --- | --- | --- | --- | --- |
| Push to talk | 17 | 11 | GND, pin 9 | Hold Space |
| Briefing | 27 | 13 | GND, pin 9 | 1 |
| Calendar | 22 | 15 | GND, pin 9 | 2 |
| Inbox | 23 | 16 | GND, pin 9 | 3 |

Inbox is the fourth button and is enabled by default. Each LED gets its own series resistor on the 5 V side. Switch commons and LED cathodes share the single GND lead. That is safe because the switch and the LED are separate pairs of legs on these buttons.

Leave I2C (pins 3 and 5), UART, and SPI free.

The on-screen buttons work even if a GPIO wire is missing. A finger on an on-screen preset sends `source: "touch"`. A GPIO press sends `source: "button"`.

## Set up the Pi

1. Flash the card as above, rotate the display to landscape, and boot once with the PowerConf plugged into USB.
2. Clone this project onto the Pi.
3. Install and enable the boot service:

   ```bash
   ./install.sh
   ```

   Add `--with-vosk` to also install the small English Vosk model (about 40 MB). Without it, preset buttons still work; push-to-talk will report that no speech model is installed.

4. Paste the webhook URL and bearer key into `/etc/desk-assistant/config.yaml`:

   ```yaml
   webhook:
     url: "https://your-assistant.example/webhook/…"
     key: "paste-the-bearer-key-here"
   ```

   Those two fields are the whole secret. `config.yaml` is root-owned, mode 600, and gitignored. Environment variables override the file if you would rather keep the key out of the yaml:

   ```bash
   # /etc/desk-assistant/desk-assistant.env
   DESK_WEBHOOK_URL=https://your-assistant.example/webhook/…
   DESK_WEBHOOK_KEY=paste-the-bearer-key-here
   ```

   To use the env file, add this line under `[Service]` in `/etc/systemd/system/desk-assistant.service` and run `sudo systemctl daemon-reload`:

   ```
   EnvironmentFile=-/etc/desk-assistant/desk-assistant.env
   ```

5. See the resolved settings, including the generated ntfy topic:

   ```bash
   desk-assistant --check
   ```

   Leave `ntfy.topic` empty. The first run writes a long random topic to `/var/lib/desk-assistant/topic` and sends that topic on every request. Treat the topic like a password: anyone who knows it can read replies and push text onto the screen.

6. Reboot.

   ```bash
   sudo reboot
   ```

   `desk-assistant.service` starts as your user after the network is up. `deploy/run.sh` waits up to 60 seconds for Wayland or X11, then falls back to the console (`kmsdrm`) if no desktop appears. Logs:

   ```bash
   journalctl -u desk-assistant -f
   ```

### What the assistant routine must do

The routine is not part of this repo. Point the webhook URL at it, and teach it to:

1. Check the bearer key. Return **401** when the key is wrong.
2. Return **400** when the routine is disabled.
3. On success, return **200** and `{"success": true, "runUuid": "<id>"}` as soon as the work is queued. Do not hold the HTTP call open until the answer is ready.
4. Publish the answer as the **raw message body** to `https://ntfy.sh/<reply_topic>`, using the `reply_topic` field from the POST body. The desk speaks that plain text.

This build uses public [ntfy.sh](https://ntfy.sh). Anyone who can guess the topic can read replies and push messages to the screen, so the generated topic is the secret. To self-host later, run your own ntfy server and set `ntfy.server` to its URL (for example `https://ntfy.example.com`). The desk already sends `reply_topic` on every request, so the routine does not need a fixed topic.

## Laptop test mode

Test mode never calls the real webhook. It starts a local server that accepts the POST and publishes a reply on the ntfy stream the desk is already listening to.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m desk_assistant --test
```

| Key | Action |
| --- | --- |
| Hold Space | Push to talk. With no speech model, this sends a sample sentence |
| 1, 2, 3 | Briefing, calendar, inbox |
| T, then type, Enter | Send your own text (`source: "touch"`) |
| Esc | Quit |

Click the on-screen buttons with the mouse. They count as touch.

A scripted pass drives that same path and exits. This is what `pytest` runs:

```bash
python -m desk_assistant --test --script --screenshot-dir /tmp/desk-assistant-demo
```

To point a laptop window at the **real** webhook, skip `--test`, copy `config.example.yaml` to `config.yaml`, paste the URL and key, and set:

```yaml
input_backend: keyboard
ui:
  fullscreen: false
```

## Speech and audio

Both speech-to-text and text-to-speech are chosen in config. `auto` uses the first engine that is actually installed.

| | Offline default | Also supported |
| --- | --- | --- |
| Speech-to-text | Vosk (`stt.engine: vosk`, model directory in `stt.vosk_model`) | whisper.cpp (`stt.engine: whisper_cpp`, `whisper-cli` plus a ggml tiny or base model). `mock` returns `stt.mock_transcript` |
| Text-to-speech | espeak-ng, installed by `install.sh` | Piper (`tts.engine: piper`, binary plus an `.onnx` voice and the matching `.onnx.json`). `mock` records the phrase and does not play it |

Playback prefers `paplay` (PipeWire / Pulse), then `aplay`, then `ffplay`. The microphone and the speaker are the same Anker PowerConf S330. `audio.input_match` and `audio.output_match` default to `PowerConf`. On startup the program looks for a capture device and a playback device whose name contains that text.

Capture is chosen from `arecord -l`. A card named `PowerConf` becomes `plughw:CARD=PowerConf,DEV=0`. Playback through `paplay` is chosen from `pactl list short sinks` (a sink name such as `alsa_output.usb-Anker_PowerConf_S330-00.analog-stereo`). Playback through `aplay` uses the same card name from `aplay -l`. If nothing matches, or the match is empty, that direction uses the system default device. `audio.device` is an optional exact arecord string and wins over `input_match`.

`desk-assistant --check` prints the input and output it actually selected, including when it fell back to the default.

Piper and whisper.cpp are not downloaded by the installer; the model files are large. Point the config at the paths once you have them.

## Screen

The Touch Display 2 panel is natively 720×1280, portrait. This desk uses it rotated to a 1280×720 landscape desktop. The UI is drawn for that size and scales with the window, so the same layout still fits an 800×480 surface. It shows idle, listening, transcribing, thinking, speaking, and a problem state, plus the last request, the last reply, and a notification line. `ui.width` and `ui.height` default to 1280 and 720. `ui.driver` can be `auto`, `wayland`, `x11`, or `kmsdrm`.

Current Raspberry Pi OS runs the desktop on Wayland with the labwc compositor. Rotate the panel there, not in the desk program. The desk then opens a fullscreen window on a desktop that is already 1280×720.

### Rotate to landscape

On the desktop:

1. Open Preferences, Control Centre, Screens.
2. Right-click the Touch Display 2. It is usually labelled `DSI-1`. Use `DSI-2` if the cable is in the other port. `wlr-randr` prints the name.
3. Choose Orientation, then **Left** or **Right**. Left is 90° clockwise and Right is 270° clockwise. Pick the one that is upright in the SmartiPi stand. Both turn the 720×1280 panel into a 1280×720 desktop.
4. Select Apply, then OK.

Control Centre stores that as a kanshi profile for labwc, in `~/.config/kanshi/config`. The panel mode stays the native portrait mode. The transform is what makes the desktop landscape:

```
profile {
    output DSI-1 enable mode 720x1280@60 position 0,0 transform 90
}
```

`transform 90` matches Left. `transform 270` matches Right. To try a rotation without rebooting, from a terminal on the desktop:

```bash
wlr-randr --output DSI-1 --transform 90
```

A console-only rotate in `/boot/firmware/cmdline.txt` (`video=DSI-1:720x1280@60,rotate=90`) turns the text console and does not rotate the Wayland desktop. Do not use it for this UI.

### Touch

The panel's touchscreen is a libinput device, typically a Goodix capacitive touchscreen. On the desktop it is mapped to the DSI output, and labwc transforms touch coordinates with that output. After you rotate the screen, a finger lines up with the landscape picture.

If a touch lands in the portrait position after the picture has rotated, bind the touch device to the same output. In Control Centre, Screens, right-click the display and set Touchscreen to the Touch Display 2 device, then Apply. That writes a labwc entry in `~/.config/labwc/rc.xml`:

```xml
<touch deviceName="10-005d Goodix Capacitive TouchScreen" mapToOutput="DSI-1" mouseEmulation="yes"/>
```

The `deviceName` on your Pi may differ. Use the name Control Centre offers. `libinput list-devices` shows it too.

The desk receives those events as ordinary mouse clicks. An on-screen preset is `source: "touch"`. The arcade buttons stay on GPIO and send `source: "button"`.

The `invx`, `invy`, and `swapxy` options on `dtoverlay=vc4-kms-dsi-ili9881-7inch` are only for touch when there is no desktop. Leave them unset when labwc is rotating the output. Setting both rotates the finger twice.

If the systemd service cannot see the desktop, copy `deploy/desk-assistant.desktop` into `~/.config/autostart/` (replace `__INSTALL_DIR__`) so it starts inside the graphical session. Do not also leave the service running, or you will have two copies. The second one exits only if you stop the service.

## Configuration

`config.example.yaml` lists every field. Search order:

1. `--config` or `DESK_CONFIG`
2. `./config.yaml`
3. `~/.config/desk-assistant/config.yaml`
4. `/etc/desk-assistant/config.yaml`

| Variable | Overrides |
| --- | --- |
| `DESK_WEBHOOK_URL` | `webhook.url` |
| `DESK_WEBHOOK_KEY` | `webhook.key` |
| `DESK_NTFY_SERVER` | `ntfy.server` |
| `DESK_NTFY_TOPIC` | `ntfy.topic` |
| `DESK_REPLY_TIMEOUT` | `reply_timeout_seconds` |
| `DESK_FULLSCREEN` | `ui.fullscreen` (`1`, `true`, `yes`) |
| `DESK_STATE_DIR` | where the generated topic is stored |
| `DESK_INPUT` | `input_backend` (`auto`, `gpio`, `keyboard`) |

`audio.input_match` and `audio.output_match` select the PowerConf by name. There is no environment override for them.

`speak_notifications: true` also speaks idle notifications. The default is to show them only.

## Tests

```bash
pytest
```

`tests/test_webhook.py` covers the bearer header, the JSON body, 200, 400, 401, other HTTP errors, and network failures. `tests/test_ntfy.py` covers the JSON and SSE parser and a live subscribe / drop / resume against the mock server. `tests/test_e2e.py` runs the desk against that server: button, touch, voice, disabled routine, bad key, notification, timeout, and the scripted laptop UI.

## Layout

```
config.example.yaml          committed template
install.sh                   Pi installer, enables systemd
deploy/desk-assistant.service
deploy/run.sh                waits for Wayland, X11, or kmsdrm
deploy/desk-assistant.desktop
src/desk_assistant/
  app.py                     idle / listen / think / speak state machine
  webhook.py                 HTTPS client
  ntfy.py                    JSON-stream subscriber
  audio.py                   arecord, Vosk, whisper.cpp, Piper, espeak-ng
  gpio_input.py              arcade buttons via gpiozero
  ui.py                      fullscreen status screen
  mock_server.py             webhook + ntfy stand-in for --test
  runtime.py                 wires a run, including the laptop demo
tests/
```

## Troubleshooting

| What you see | What to check |
| --- | --- |
| "The assistant routine is turned off" | The webhook returned 400. Enable the routine |
| "The webhook key was rejected" | 401. The key in `config.yaml` or `DESK_WEBHOOK_KEY` does not match |
| "Couldn't reach the assistant" | URL, DNS, and that the Pi is online. `--check` prints the URL |
| Thinking, then "No reply arrived" | The routine accepted the POST but nothing was published to `reply_topic`. `--check` prints the topic |
| Buttons do nothing physical | `gpioinfo` or group `gpio`. Reboot after `install.sh`. A HAT using BCM 17, 27, 22, or 23 will collide. The display power cable should stay on pins 2 and 6 |
| LEDs stay dark | 5 V and GND on the LED legs, with the series resistor. The program does not switch the LEDs |
| No speech | `espeak-ng` installed, PowerConf volume up, and `--check` shows an output device that matched `PowerConf`. `paplay` has to be able to play a wav on that sink |
| No transcript | `install.sh --with-vosk`, or set `stt.whisper_*`. `--check` shows the capture device. `arecord -l` should list `PowerConf` |
| Touch is offset after rotation | Map the touchscreen to the DSI output in Control Centre. Do not also set `swapxy` on the display overlay |
| Blank screen from the service | `journalctl -u desk-assistant`. Autologin must be on, or set `ui.driver: kmsdrm` and stop the desktop |

## Limits of this version

- The reply is the next plain-text ntfy message while the desk is waiting. There is no run id inside the reply, because the routine sends plain text.
- Public ntfy topics are unauthenticated. Use a self-hosted ntfy with access control if the replies are private.
- Push-to-talk needs a local model. The installer does not download Piper or whisper.cpp models.
- GPIO needs Raspberry Pi OS. Everywhere else the keyboard backend is used.
