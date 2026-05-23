# steprtool

Web-based control for a StepIR Step 100 antenna controller and a hy-gain DCU-2
rotator controller, used by Jefferson Amateur Radio Club operators on a Flex
6300 setup. Runs on Windows 11. One computer (computer A) has the physical
USB-to-serial connections to both devices; operators on the LAN (or remotely
via Tailscale) connect with a browser to the steprtool web UI to issue commands.

## Quick start (mock mode, no hardware required)

```cmd
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python run.py
```

Open https://localhost:8443/ in a browser. Your browser will warn about the
self-signed certificate; accept it. Enter a name and callsign in the modal,
then exercise the controls. With `STEP100_PORT=MOCK` and `DCU2_PORT=MOCK` in
`.env` (the defaults), commands are computed and the bytes shown in the Last
Action panel, but nothing is written to a serial port.

## Configuration

All settings live in `.env`. Copy `.env.example` and edit:

| Variable                    | Default        | Notes |
|-----------------------------|----------------|-------|
| `WEB_HOST`                  | `0.0.0.0`      | listen address |
| `WEB_PORT`                  | `8443`         | HTTPS port |
| `CERT_FILE` / `KEY_FILE`    | `certs/*.pem`  | auto-generated self-signed on first run |
| `STEP100_PORT`              | `MOCK`         | COM port (e.g. `COM3`) or `MOCK` |
| `STEP100_BAUD`              | `4800`         | |
| `STEP100_BYTESIZE`          | `8`            | |
| `STEP100_PARITY`            | `N`            | N/E/O/M/S |
| `STEP100_STOPBITS`          | `1`            | 1, 1.5, or 2 |
| `STEP100_DTR` / `STEP100_RTS` | `false`      | line states after open |
| `STEP100_WAIT_SECONDS`      | `10`           | lock duration after a command |
| `STEP100_DIRECTION`         | `normal`       | element pattern: `normal`, `180` |
| `DCU2_PORT`                 | `MOCK`         | |
| `DCU2_BAUD`                 | `4800`         | |
| ...                         |                | (same shape as Step 100) |
| `DCU2_WAIT_SECONDS`         | `60`           | rotator can take up to ~1 min |

To switch a device from mock to live, change its `*_PORT` from `MOCK` to the
real COM port name and restart steprtool.

## Wire protocols

**Step 100** — 11-byte fixed frame, no response from device, write-and-wait:

| Offset | Value | Meaning |
|---|---|---|
| 0 | `0x40` | `@` |
| 1 | `0x41` | `A` (unit address) |
| 2 | `0x00` | constant |
| 3..5 | freq | tens-of-Hz, 24-bit big-endian (UI accepts kHz; multiplied by 100) |
| 6 | `0x00` | constant |
| 7 | direction | `0x00` normal / `0x40` 180  |
| 8 | `0x52` | `R` (retune command) |
| 9 | `0x00` | constant |
| 10 | `0x0D` | CR |

Home and Calibrate command bytes are **not yet defined** — those buttons exist
in the UI and return a `NOT IMPLEMENTED` response with no bytes and no lock.

**DCU-2** — ASCII command set shared with the DCU-1, 4800 baud, 8-N-1:

* `AP1xxx;` sets target bearing (xxx = 000..359, three digits zero-padded)
* `AM1;` starts rotation
* steprtool sends them combined as `AP1xxx;AM1;`

The DCU-2 does not report position back, so steprtool cannot tell when motion
has finished. Instead it locks the device for `DCU2_WAIT_SECONDS` after each
command (default 60s, the worst-case full-rotation time).

## UI

* **Step 100 panel**: frequency input (kHz), buttons for Change Frequency,
  Home, Calibrate. Only Change Frequency requires the input.
* **DCU-2 panel**: azimuth input (0–359°), eight compass quick-fill buttons
  (N, NE, E, SE, S, SW, W, NW), Change Direction button.
* **Last Action panel**: device, action, operator, status, detail, bytes sent
  (hex). Updates in place; broadcast to all connected browsers via Socket.IO.
* **Countdown timer**: while a device is locked, the time remaining is shown
  next to that device's status. All connected browsers see the same countdown.
* **Operator identification**: on first visit, a modal asks for name and
  callsign. Stored in browser localStorage. Sent with every command and
  written to the log.

## Concurrency

* Per-device lock. While one device is locked, commands to it return HTTP 409
  with seconds remaining. Commands to the other device are unaffected.
* All connected clients see the same lock state and the same Last Action via
  Socket.IO; "last write wins" if two browsers race the same device.

## Logging

* `logs/steprtool.log` — application log, rotated at 2 MB, 5 backups.
* `logs/service.out.log` / `logs/service.err.log` — written by NSSM when run
  as a service.

Every command logs operator, device, action, bytes, and status.

## Running as a Windows service

Install NSSM (https://nssm.cc/), put `nssm.exe` on `PATH`, then from an
Administrator command prompt in the project root:

```cmd
.\scripts\install-service.bat
sc start steprtool
```

To remove:

```cmd
.\scripts\uninstall-service.bat
```

## File layout

```
run.py                          entry point
.env.example                    template config
requirements.txt                Python deps
steprtool/
  app.py                        Flask + Socket.IO app factory
  config.py                     .env loader and validator
  devices/
    base.py                     lock / wait timer / serial / broadcast
    step100.py                  Step 100 command builder + actions
    dcu2.py                     DCU-2 command builder + actions
  routes/
    api.py                      JSON command endpoints
    pages.py                    index template route
  static/css/style.css
  static/js/app.js
  templates/index.html
scripts/
  generate-cert.py              self-signed cert generator
  install-service.bat
  uninstall-service.bat
certs/                          generated cert + key (gitignored)
logs/                           rotated logs (gitignored)
```
