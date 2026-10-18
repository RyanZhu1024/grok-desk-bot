# Desk Assistant

A small push-to-talk desk assistant for a Raspberry Pi 5. Arcade buttons and a touchscreen send text to a Grok Bot webhook. The reply comes back as plain text on an [ntfy](https://ntfy.sh) topic, shows on the screen, and is spoken out loud.

The same program runs on a laptop. Keyboard keys stand in for the GPIO buttons, and `--test` answers with a local mock of the webhook so nothing leaves the machine.

A second build, [Haro](#haro), is screenless. It runs on a Raspberry Pi Zero 2 W inside a small round shell and uses the same webhook and ntfy path.

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

| Piece | Notes |
| --- | --- |
| Raspberry Pi 5, 4 GB or more, and the official 27 W USB-C supply | Raspberry Pi OS 64-bit **with desktop**, autologin on |
| microSD, 32 GB or larger | |
| HDMI touchscreen, about 3.5 to 5 inch, 800×480 | Waveshare 4.3" or 5" HDMI + USB touch keeps the GPIO header free. A DSI panel is fine too if it does not reuse the 40-pin header |
| USB microphone | |
| USB speaker, or a USB audio adapter and a small powered speaker | **The Pi 5 has no 3.5 mm jack.** HDMI audio also works if the display has speakers |
| 3 or 4 momentary arcade buttons | One is push-to-talk. Two presets are the usual set. A third preset is optional |
| Female-to-female jumper wires | |
| Optional 220 Ω resistors | Only if the buttons have LEDs. Power LEDs from 3.3 V, never 5 V, and never into a GPIO pin |

## Wiring

BCM numbering, internal pull-up, press connects the pin to ground. No external resistor on the switch.

The header is shown with pin 1 at the top left, the way `pinout` prints it. Pins on the left column are the odd numbers.

```
          3V3 (1)  (2)  5V
       GPIO2  (3)  (4)  5V
       GPIO3  (5)  (6)  GND
       GPIO4  (7)  (8)  GPIO14
         GND  (9)  (10) GPIO15     pin 9  GND ---- talk button ---- pin 11 GPIO17   push-to-talk
      GPIO17 (11)  (12) GPIO18
      GPIO27 (13)  (14) GND        pin 14 GND ---- briefing ------- pin 13 GPIO27
      GPIO22 (15)  (16) GPIO23     pin 20 GND ---- calendar ------- pin 15 GPIO22
         3V3 (17)  (18) GPIO24     pin 25 GND ---- inbox (optional) pin 16 GPIO23
         ...
         GND (25)
```

| Control | BCM | Physical pin | Other leg | Laptop key |
| --- | --- | --- | --- | --- |
| Push to talk | 17 | 11 | GND, pin 9 | Hold Space |
| Briefing | 27 | 13 | GND, pin 14 | 1 |
| Calendar | 22 | 15 | GND, pin 20 | 2 |
| Inbox (optional) | 23 | 16 | GND, pin 25 | 3 |

Lit arcade buttons have a separate pair of LED legs. Wire those to **3.3 V (pin 1)** through a 220 Ω resistor and to a GND pin. Do not connect 5 V to the LED if it shares any connection with a GPIO pin, and do not connect the LED to a GPIO pin.

Leave I2C (pins 3 and 5), UART, and SPI free. These four GPIO pins are enough.

The on-screen buttons work even if a GPIO wire is missing. A finger on an on-screen preset sends `source: "touch"`. A GPIO press sends `source: "button"`.

## Set up the Pi

1. Flash Raspberry Pi OS 64-bit with desktop. Enable autologin so a display session exists at boot. Boot once with the screen, mic, and speaker connected.
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

Playback prefers `paplay` (PipeWire / Pulse), then `aplay`, then `ffplay`. Set `tts.player` if you need a specific device. Set `audio.device` to an arecord device such as `plughw:1,0` when the USB mic is not the default. `arecord -l` lists capture devices.

Piper and whisper.cpp are not downloaded by the installer; the model files are large. Point the config at the paths once you have them.

## Screen

The UI is a fullscreen pygame window aimed at 800×480. It shows idle, listening, transcribing, thinking, speaking, and a problem state, plus the last request, the last reply, and a notification line. Change `ui.width`, `ui.height`, and `ui.fullscreen` if the panel is a different size. `ui.driver` can be `auto`, `wayland`, `x11`, or `kmsdrm`.

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

`speak_notifications: true` also speaks idle notifications. The default is to show them only.

## Tests

```bash
pytest
```

`tests/test_webhook.py` covers the bearer header, the JSON body, 200, 400, 401, other HTTP errors, and network failures. `tests/test_ntfy.py` covers the JSON and SSE parser and a live subscribe / drop / resume against the mock server. `tests/test_e2e.py` runs the desk against that server: button, touch, voice, disabled routine, bad key, notification, timeout, and the scripted laptop UI. `tests/test_haro.py` covers the Haro target field, the acknowledgement plus reply, the console eyes, and the low-battery warning.

## Layout

```
config.example.yaml          desk template
config.haro.example.yaml     Haro template
install.sh                   Pi installer (--haro for the voice build)
deploy/desk-assistant.service
deploy/haro.service          screenless Haro service
deploy/run.sh                waits for Wayland, X11, or kmsdrm
deploy/desk-assistant.desktop
src/desk_assistant/
  app.py                     desk idle / listen / think / speak state machine
  haro.py                    screenless hold-to-talk loop
  eyes.py                    APA102 eyes, or a console stand-in
  battery.py                 PiSugar 3 level, when the server is running
  webhook.py                 HTTPS client
  ntfy.py                    JSON-stream subscriber
  audio.py                   arecord, Vosk, whisper.cpp, Piper, espeak-ng
  gpio_input.py              arcade buttons via gpiozero
  ui.py                      fullscreen status screen (desk only)
  mock_server.py             webhook + ntfy stand-in for --test
  runtime.py                 wires a desk run, including the laptop demo
tests/
```

## Troubleshooting

| What you see | What to check |
| --- | --- |
| "The assistant routine is turned off" | The webhook returned 400. Enable the routine |
| "The webhook key was rejected" | 401. The key in `config.yaml` or `DESK_WEBHOOK_KEY` does not match |
| "Couldn't reach the assistant" | URL, DNS, and that the Pi is online. `--check` prints the URL |
| Thinking, then "No reply arrived" | The routine accepted the POST but nothing was published to `reply_topic`. `--check` prints the topic |
| Buttons do nothing physical | `gpioinfo` or group `gpio`. Reboot after `install.sh`. A HAT using BCM 17, 27, 22, or 23 will collide |
| No speech | `espeak-ng` installed, volume up, and `paplay` can play a wav. Pi 5 needs USB or HDMI audio |
| No transcript | `install.sh --with-vosk`, or set `stt.whisper_*`. `arecord -l` for the mic |
| Blank screen from the service | `journalctl -u desk-assistant`. Autologin must be on, or set `ui.driver: kmsdrm` and stop the desktop |

## Haro

Haro is a voice-only build for a Raspberry Pi Zero 2 W (512 MB) inside a small round shell. There is no screen. Hold the button to talk and release to send. The POST uses the same webhook as the desk, with `source` set to `haro` and a `target` field:

```json
{"text": "Rune what time is it", "source": "haro", "reply_topic": "desk-…", "target": "rune"}
```

A transcript that starts with **Rune** sets `target` to `rune`. One that starts with **Athelas** sets `target` to `athelas`. Anything else, including "Hey Rune", sets `target` to `ollivander`. The full transcript is still the `text` field.

Replies arrive on the ntfy topic and are spoken. A short acknowledgement such as `Asking Rune…` can arrive before the real answer. Haro speaks that, keeps waiting, and speaks the answer too. The desk still treats the first ntfy message as the whole reply.

### Parts

| Piece | Notes |
| --- | --- |
| Raspberry Pi Zero 2 W, 512 MB | Raspberry Pi OS **Lite** 64-bit. No desktop |
| ReSpeaker 2-Mics Pi HAT | Microphones, speaker amplifier, user button on GPIO17, 3 APA102 RGB LEDs |
| Small speaker | Plugged into the HAT's speaker output |
| PiSugar 3 | Battery power for the Zero. The level is read if PiSugar Power Manager is running |
| Round shell, about 10–12 cm inside | The stack has to fit with air around the speaker and the mics |

### Speech on a Zero

Speech-to-text defaults to the Vosk small English model. That is the engine that fits in 512 MB. `stt.engine` is still pluggable (`vosk`, `whisper_cpp`, or `mock`). Speech output defaults to espeak-ng through `aplay` on the ReSpeaker card. A Piper voice can be selected in config, but even a small one is often too slow on this board and can push it out of memory. Stay with espeak-ng unless you have measured a tiny voice and can accept the pause.

### Eyes and battery

The three APA102s are the eyes. They are dim at idle, green while listening, pulsing while thinking, bright while speaking, and red on an error. The program does not open a window. On a laptop, `--test` prints the same moods as `[haro] eyes: …`.

If PiSugar Power Manager is running, Haro asks it for `get battery` on `/tmp/pisugar-server.sock`, then on `127.0.0.1:8423`. At or below 20% it says "Battery is low." once, and again only after the level has climbed back above that line. If the server is not installed, Haro skips the warning.

### ReSpeaker on current Raspberry Pi OS Lite 64-bit

Current Lite images (kernel 6.1 and later, including 6.12) already ship the WM8960 codec driver. The old `seeed-voicecard` DKMS build often fails on these kernels. Use Seeed's overlay instead. This is the v2 board. `/boot/firmware/config.txt` is the file both Lite and desktop images use now.

```bash
sudo apt update
sudo apt install -y git device-tree-compiler
git clone https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git
cd seeed-linux-dtoverlays/overlays/rpi
dtc -I dts -O dtb -o respeaker-2mic-v2_0.dtbo respeaker-2mic-v2_0-overlay.dts
sudo cp respeaker-2mic-v2_0.dtbo /boot/firmware/overlays/
echo "dtoverlay=respeaker-2mic-v2_0" | sudo tee -a /boot/firmware/config.txt
sudo raspi-config nonint do_spi 0
sudo reboot
```

`arecord -l` and `aplay -l` should then show a card named `seeed2micvoicec`. The sample config points both the microphone and the speaker at `plughw:CARD=seeed2micvoicec,DEV=0`. The onboard button is GPIO17 and is active-low. The LEDs are the three APA102s on SPI bus 0, device 1 (`/dev/spidev0.1`), which is why SPI has to be on.

An older v1 HAT can stay silent with that overlay. In that case use HinTak's `seeed-voicecard` branch that matches `uname -r` (for a 6.12 kernel, `git clone -b v6.12 https://github.com/HinTak/seeed-voicecard`) and run its `install.sh`. Do not mix that DKMS driver with the v2 overlay.

Install PiSugar Power Manager from the PiSugar 3 docs so `pisugar-server` is running. Haro only needs the `get battery` command.

Then:

```bash
./install.sh --haro
```

That installs Vosk's small English model, skips the desk UI packages, enables `haro.service`, and turns SPI on in `config.txt` if it is not already. `haro --check` prints the topic, the audio devices, the button, the LEDs, and the battery percent when the power manager answers.

### Fitting a 10–12 cm shell

The Zero 2 W is 65 × 30 mm. PiSugar 3 sits under it. The ReSpeaker HAT plugs onto the 40-pin header. Center that stack. It is a few centimetres tall, which fits a 10 cm sphere if nothing else is stacked on the HAT and the speaker sits beside it rather than on top.

- Point the two mics at two small holes. Do not cover them.
- Point the speaker cone at a ring of holes. Leave air behind the cone. Do not press it against the battery.
- The APA102s are the eyes. A clear window or a short light pipe is enough. They are not wired out to a separate board.
- The HAT button is hard to reach once the shell is closed. Leave a finger hole over it, or wire another momentary button in parallel from GPIO17 to ground. Hold still means talk, and release still means send.
- Cut a slot for the PiSugar charge port.
- SPI (eyes), I2C (PiSugar), and I2S (audio) are separate buses. They can all be enabled. This build does not use a display.

### Laptop test

```bash
python -m desk_assistant --variant haro --test
```

Press space to start talking and space again to send. A terminal cannot see a key release, so this toggles the microphone. The Pi button is still hold-to-release. `q` quits. Eye states print on the console. The scripted check the tests run is:

```bash
python -m desk_assistant --variant haro --test --script
```

`haro` is the same entry point as `--variant haro`.

## Limits of this version

- The reply is the next plain-text ntfy message while the desk is waiting. There is no run id inside the reply, because the routine sends plain text.
- Public ntfy topics are unauthenticated. Use a self-hosted ntfy with access control if the replies are private.
- Push-to-talk needs a local model. The installer does not download Piper or whisper.cpp models.
- GPIO needs Raspberry Pi OS. Everywhere else the keyboard backend is used.
