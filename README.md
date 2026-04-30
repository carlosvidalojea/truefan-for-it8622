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

### 3. Update the NAS IP address

Edit `app/mail_webhook.py` and `app/fan.py` replacing `192.168.0.157` with your TrueNAS IP address:

In `fan.py`:
```python
MAIL_WEBHOOK = "http://<YOUR_NAS_IP>:5003/send"
```

## Installation via TrueNAS Apps

Go to **Apps > Discover Apps > Custom App** and paste the following `docker-compose.yaml`:

```yaml
services:
  truefan:
    container_name: truefan
    image: carlosvidalojea/truefan-for-it8622:latest
    ports:
      - '5002:5002'
    environment:
      - TZ=Europe/Madrid
    privileged: true
    restart: unless-stopped
    volumes:
      - /sys:/sys
      - /dev:/dev
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
    ├── fan_profile.conf    # Default profile
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

HDD override: `PWM = 6 × HDD_avg − 104` — applied if higher than the CPU-based value.

Emergency: if any temperature exceeds 55°C, PWM is set to 255 regardless of profile.

The control loop runs every 30 seconds.

### Temperature alert thresholds

| Sensor | Alert | Recovery |
|--------|-------|----------|
| CPU | 80°C | 75°C |
| HDD | 50°C | 45°C |
| NVMe | 65°C | 60°C |

Alerts fire once when the threshold is crossed. Recovery notification is sent when the temperature drops 5°C below the threshold (hysteresis).

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
