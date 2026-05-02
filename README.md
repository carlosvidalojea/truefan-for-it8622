# TrueFan for IT8622 — Fan Control Dashboard for TrueNAS

Intelligent fan speed controller and monitoring dashboard for TrueNAS systems using the IT8622 chip. This project is a fork version of [Truefan v0.2.0](https://github.com/Rocketplanner83/truefan/releases/tag/v0.2.0) by Rocketplanner83 with extensive modifications for TrueNAS systems using the **IT8622 SuperIO chip**.
All credit for the original concept and base implementation goes to the original author.

## Features

- Full compatibility with TrueNAS environments
- Fan management with profiles
- PWM manual control
- Web dashboard with real-time temperature and fan speed graphs
- Web dashboard enhancements (graphs, dark mode, CSV export)

## What's different from the original

- Rewritten sensor parser compatible with the IT8622 driver output format
- Temperature-based PWM control using direct formulas per profile (no PID loop)
- Three profiles: Silent, Balanced, Performance — each with its own RPM floor and formula
- Named temperature display: CPU, Motherboard, HDD 1–4, NVMe 1–2
- Multi-line temperature graph (CPU, HDD avg., NVMe avg.)
- CPU load bar with Now / 5 min. / 15 min. trend markers
- Email alerts via TrueNAS SMTP when temperatures exceed thresholds, with hysteresis
- Send Status button for on-demand email reports
- Basic HTTP authentication
- PWM manual control with hardware Auto fallback
- Dark mode
- CSV export with semicolon separator (Excel compatible)
- Zero RPM glitch filter (3 consecutive readings required to confirm fan stop)
- Automatic host IP detection via `extra_hosts` — no manual network configuration required
- Enhanced HDD protection with per-disk emergency threshold and absolute PWM priority
- Removed container management buttons (restart, shutdown)

## Hardware Requirements

- TrueNAS system with IT8622 (or compatible) SuperIO chip
- Fan connected to PWM channel 3 (`pwm3`)
- `it87` kernel module support

## Prerequisites — run once on the TrueNAS host

### 1. Load the IT8622 driver at boot

Go to **System > Advanced > Init/Shutdown Scripts > Add**:
- Type: `Command`
- When: `Post Init`
- Command:
```
modprobe it87 force_id=0x8622
```

### 2. Set up the email relay

The email system requires a small webhook server running on the TrueNAS host (outside the container), because `midclt` — the TrueNAS mail command — is not available inside Docker containers.

Copy the webhook to your NAS storage:
```bash
cp app/mail_webhook.py /mnt/nas/apps/truefan/mail_webhook.py
```

Then add a second Init/Shutdown Script:
- Type: `Command`
- When: `Post Init`
- Command:
```
nohup python3 /mnt/nas/apps/truefan/mail_webhook.py &
```

> The container reaches the host webhook via `host-gateway` — automatically resolved by Docker's `extra_hosts` mechanism. No manual IP configuration needed.

## Installation via TrueNAS Apps

Go to **Apps > Discover Apps > Custom App** and paste the following `docker-compose.yaml`:

```yaml
services:
  truefan:
    container_name: truefan
    environment:
      - TZ=Europe/Madrid
    extra_hosts:
      - host-gateway:host-gateway
    image: carlosvidalojea/truefan-for-it8622:latest
    network_mode: bridge
    ports:
      - '5002:5002'
    privileged: true
    restart: unless-stopped
    volumes:
      - /sys/class/hwmon:/sys/class/hwmon:ro
      - /etc/sensors3.conf:/etc/sensors3.conf:ro
x-portals:
  - host: 0.0.0.0
    name: Web UI
    path: /
    port: 5002
    scheme: http
```

> ⚠️ Adjust `TZ` to your local timezone before deploying.

Access the dashboard at `http://<NAS_IP>:5002`

Default credentials: `admin` / `truefan`

### Security improvements in this configuration

Compared to a naive Docker setup, this yaml minimises the attack surface:

- **`/sys/class/hwmon` mounted read-only** — only the hardware monitoring subsystem is exposed, not the full `/sys` tree
- **`/dev` not mounted** — sensor data is read via sysfs without needing raw device access
- **`network_mode: bridge`** — the container keeps its own isolated network namespace; only the specific `host-gateway` hostname is added to reach the host webhook
- **`privileged: true`** is still required for PWM write access — there is no finer-grained alternative for this hardware

### Profile persistence (optional)

By default the container starts with the `balanced` profile on every restart. If you want the active profile to survive container restarts and recreations, mount a persistent config file:

**1. Create the file on the host:**
```bash
echo "profile=balanced" | sudo tee /mnt/nas/apps/truefan/fan_profile.conf
```

**2. Add the volume to the yaml:**
```yaml
volumes:
  - /sys/class/hwmon:/sys/class/hwmon:ro
  - /etc/sensors3.conf:/etc/sensors3.conf:ro
  - /mnt/nas/apps/truefan/fan_profile.conf:/app/fan_profile.conf
```

Without this volume the container always starts with `balanced`. The profile can still be changed at runtime via the dashboard — it just resets to `balanced` on the next restart.

## Build from source

```bash
git clone https://github.com/carlosvidalojea/truefan-for-it8622.git
cd truefan-for-it8622
docker build -t truefan-for-it8622 .
```

## File Structure

```
truefan-for-it8622/
├── Dockerfile
├── entrypoint.sh           # Container startup script
├── docker-compose.yaml     # For building locally
├── README.md
└── app/
    ├── fan.py              # Fan control logic and sensor parser
    ├── server.py           # Flask web server and API routes
    ├── mail_webhook.py     # Email relay — copy to NAS host, run outside container
    ├── fan_profile.conf    # Default profile (balanced)
    ├── profiles.json
    └── templates/
        └── index.html      # Web dashboard
```

## Fan Control

### Verify hardware manually

```bash
# Load driver
modprobe it87 force_id=0x8622

# Enable manual PWM control
echo 1 | sudo tee /sys/class/hwmon/hwmon8/pwm3_enable

# Set fan speed (0–255)
echo 150 | sudo tee /sys/class/hwmon/hwmon8/pwm3

# Return to hardware automatic control
echo 2 | sudo tee /sys/class/hwmon/hwmon8/pwm3_enable
```

### PWM to RPM reference

| PWM | RPM approx. |
|-----|-------------|
| 50  | 350 rpm |
| 75  | 600 rpm |
| 100 | 800 rpm |
| 125 | 1000 rpm |
| 150 | 1200 rpm |
| 175 | 1365 rpm |
| 200 | 1550 rpm |
| 225 | 1725 rpm |
| 250 | 1900 rpm |

### Control profiles

Fan speed is calculated directly from temperature. The reference temperature is the maximum of CPU temp and average HDD temp.

| Profile | CPU formula | Min RPM | Approx. target |
|---------|-------------|---------|----------------|
| Silent | PWM = 3T − 51 | 800 rpm | ~44°C |
| Balanced | PWM = 2T + 48 | 1000 rpm | ~40°C |
| Performance | PWM = T + 152 | 1200 rpm | ~36°C |

HDD override: `PWM = 15 × HDD_avg − 500` — applied if higher than the CPU-based value.

### Emergency protection

If any sensor breaches its emergency threshold, PWM is set to **255 immediately**, with absolute priority over all profile formulas. This check runs at the start of every control cycle before any formula is evaluated.

| Sensor | Emergency threshold |
|--------|-------------------|
| CPU | 80°C |
| HDD (each disk individually) | 55°C |
| NVMe | 70°C |

HDDs are evaluated individually — a single disk reaching 55°C triggers the emergency regardless of the others. This reflects the fact that HDDs can suffer permanent damage before CPU thermal throttling becomes active.

The control loop runs every 30 seconds.

### Temperature alert thresholds

Email alerts are sent as early warnings, below the emergency thresholds:

| Sensor | Alert | Recovery |
|--------|-------|----------|
| CPU | 80°C | 75°C |
| HDD | 50°C | 45°C |
| NVMe | 65°C | 60°C |

Alerts fire once when the threshold is crossed. A recovery notification is sent when the temperature drops 5°C below the threshold (hysteresis), preventing repeated alerts from normal temperature fluctuations.

## Web Interface

| Section | Description |
|---------|-------------|
| Status | Uptime and CPU load bar with Now / 5 min. / 15 min. trend markers |
| Fan Profiles | Silent, Balanced, Performance — active profile highlighted in green |
| PWM Manual Control | Slider (0–255) with Apply; Auto returns fan to hardware control |
| Fan Speeds | Live RPM list and historical graph |
| Temperatures | All sensor readings and three-line graph (CPU, HDD avg., NVMe avg.) |
| Download CSV | Exports session history, semicolon-separated |
| Send Status | On-demand email with current temperatures, fan speed and active profile |
| Dark mode | Toggle via 🌓, persisted in browser |

## Sensors

| Label | Source | Notes |
|-------|--------|-------|
| CPU | Package id 0 | coretemp |
| Motherboard | temp3 | it8622 |
| HDD 1–4 | temp1 | drivetemp-scsi-0/1/2/3 |
| NVMe 1–2 | Composite | nvme-pci |

## Known Limitations

- `hwmon8` is hardcoded in `fan.py`. If the kernel reassigns hwmon numbers after an update, the path must be updated manually.
- The IT8622 driver occasionally reports 0 RPM glitches. The dashboard filters single-cycle zero readings; three consecutive zero readings are treated as a real fan stop.
- The email webhook must be running on the host for alerts to work. It starts automatically via Init Scripts after each reboot.
- After activating PWM Manual Control, returning to hardware automatic control via the Auto button may not work in all cases. If the fan does not respond, a container or system restart may be required to restore automatic control.
