# TrueFan for IT8622 — Fan Control Dashboard for TrueNAS

Intelligent fan speed controller and monitoring dashboard for TrueNAS systems using the IT8622 chip. This project is a fork version of [Truefan v0.2.0](https://github.com/Rocketplanner83/truefan/releases/tag/v0.2.0) by Rocketplanner83 with extensive modifications for TrueNAS systems using the **IT8622 SuperIO chip**.
All credit for the original concept and base implementation goes to the original author.

## DISCLAIMER: 

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Features

- Compatibility with TrueNAS (Tested on Community Edition 25.10.3 - Goldeye)
- Fan management with profiles schedule
- PWM control with high temperature protection
- Web dashboard with real-time temperature and fan speed graphs
- Email alerts 
- Configurale log export

## What's different from the original

- Rewritten sensor parser compatible with the IT8622 driver output format
- Temperature-based PWM control using direct formulas per profile (no PID loop)
- Three profiles: Silent, Balanced, Cool — each with its own RPM floor and formula
- Named temperature display: CPU, Motherboard, HDD 1–4, NVMe 1–2
- Multi-line temperature graph (CPU, HDD avg., NVMe avg.)
- CPU load bar with Now / 5 min. / 15 min. trend markers
- Scheduled profiles — 7×24 grid to assign a profile per day and hour
- Email alerts via TrueNAS SMTP when temperatures exceed thresholds, with hysteresis
- Send Status Email button for on-demand status reports
- Basic HTTP authentication
- PWM manual control with hardware Auto fallback
- Emergency PWM protection — any sensor breach triggers PWM 255 with absolute priority
- Dark mode
- Log download — full log or configurable CSV export
- Log rotation at 32,768 lines
- Zero RPM glitch filter
- Dynamic IT8x hwmon detection — no hardcoded hwmon path
- Host agent architecture — no `privileged: true` required in the container
- All icons and Chart.js served locally — no external CDN dependencies
- Removed container management buttons (restart, shutdown) and system uptime.

## Hardware Requirements

- TrueNAS system with IT8622 (or compatible) SuperIO chip (Tested on a Terramaster F4-423)
- Fan connected to PWM channel 3 (`pwm3`)
- `it87` kernel module support

## Architecture

TrueFan uses two small Python agents running on the TrueNAS host to handle operations that require host-level access:

```
Container (no privileged)          TrueNAS Host
─────────────────────────          ────────────────────────────────
Reads sensors via hwmon:ro    
Calculates PWM                →    truefan_agent.py (port 5003, root)
                                   └── writes PWM to sysfs
Sends alert / status email    →    mail_webhook.py (port 5004, user)
                                   └── calls midclt → TrueNAS SMTP
```

The container communicates with the host agents via `host-gateway`, automatically resolved by Docker's `extra_hosts` mechanism.

## Installation

### Step 1 — Copy host agent files to the NAS

Create a directory on your NAS storage and copy the two agent files:

```bash
mkdir -p /mnt/nas/apps/truefan
cp app/truefan_agent.py /mnt/nas/apps/truefan/
cp app/mail_webhook.py  /mnt/nas/apps/truefan/
```

Or download directly from the repository:

```bash
mkdir -p /mnt/nas/apps/truefan
curl -o /mnt/nas/apps/truefan/truefan_agent.py \
  https://raw.githubusercontent.com/carlosvidalojea/truefan-for-it8622/main/app/truefan_agent.py
curl -o /mnt/nas/apps/truefan/mail_webhook.py \
  https://raw.githubusercontent.com/carlosvidalojea/truefan-for-it8622/main/app/mail_webhook.py
```

### Step 2 — Register Init/Shutdown Scripts

Go to **System > Advanced > Init/Shutdown Scripts** and add three commands, all with **Type: Command** and **When: Post Init**:

**Script 1 — Load IT8622 driver:**
```
modprobe it87 force_id=0x8622
```

**Script 2 — Start PWM agent (requires root for sysfs writes):**
```
sudo sh -c 'python3 /mnt/nas/apps/truefan/truefan_agent.py >> /mnt/nas/apps/truefan/truefan_agent.log 2>&1 &'
```

**Script 3 — Start mail webhook (must run as regular user for midclt access):**
```
nohup python3 /mnt/nas/apps/truefan/mail_webhook.py &
```

> ⚠️ The mail webhook **must not** run as root — it uses `midclt` to send emails via TrueNAS SMTP, which requires the regular user context.

### Step 3 — Start agents now (no reboot needed)

```bash
modprobe it87 force_id=0x8622
sudo sh -c 'python3 /mnt/nas/apps/truefan/truefan_agent.py >> /mnt/nas/apps/truefan/truefan_agent.log 2>&1 &'
nohup python3 /mnt/nas/apps/truefan/mail_webhook.py &
```

Verify both agents are running:

```bash
# PWM agent — should return {"status":"ok"}
curl -X POST http://127.0.0.1:5003/pwm \
  -H "Content-Type: application/json" -d '{"value":128}'

# Mail agent — should send a test email
curl -X POST http://127.0.0.1:5004/mail \
  -H "Content-Type: application/json" \
  -d '{"subject":"TrueFan test","text":"Agents working"}'
```

### Step 4 — Deploy the container via TrueNAS Apps

Go to **Apps > Discover Apps > Custom App** and paste the following yaml:

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

> ⚠️ Adjust `TZ` to your local timezone.

Access the dashboard at `http://<NAS_IP>:5002`

Default credentials: `admin` / `truefan`

### Security notes

- `/sys/class/hwmon` is mounted read-only — only hardware monitoring is exposed
- `/dev` is not mounted — sensor data is read via sysfs without raw device access
- `network_mode: bridge` with `extra_hosts` — isolated network, only `host-gateway` is reachable
- `privileged: true` is **not required** — PWM writes are delegated to the host agent

### Optional — Profile and schedule persistence across restarts

By default the container starts with the `balanced` profile on every restart. To persist the active profile and schedule configuration, create the files on the host and mount them:

```bash
echo "profile=balanced" > /mnt/nas/apps/truefan/fan_profile.conf
echo '{"enabled":false,"grid":[["balanced"]*24]*7}' > /mnt/nas/apps/truefan/fan_schedule.json
```

Add these volumes to the yaml:
```yaml
volumes:
  - /sys/class/hwmon:/sys/class/hwmon:ro
  - /etc/sensors3.conf:/etc/sensors3.conf:ro
  - /mnt/nas/apps/truefan/fan_profile.conf:/app/fan_profile.conf
  - /mnt/nas/apps/truefan/fan_schedule.json:/app/fan_schedule.json
```

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
├── entrypoint.sh
├── docker-compose.yaml
├── README.md
└── app/
    ├── fan.py                  # Fan control logic, sensor parser, schedule
    ├── server.py               # Flask web server and API routes
    ├── truefan_agent.py        # PWM agent — copy to NAS host, run as root
    ├── mail_webhook.py         # Mail agent — copy to NAS host, run as user
    ├── fan_profile.conf        # Default profile (balanced)
    ├── static/
    │   └── js/
    │       └── chart.min.js   # Chart.js served locally
    └── templates/
        └── index.html          # Web dashboard
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

### Testing other IT8x drivers

This project was developed and tested with the IT8622 chip. If your system uses a different IT8x variant, you may need a different `force_id` value. To find it:

```bash
# Try loading without force_id first
modprobe it87

# Check what was detected
dmesg | grep -i it8
sensors | grep -i it8

# If not detected, try common force_id values:
# IT8620: modprobe it87 force_id=0x8620
# IT8628: modprobe it87 force_id=0x8628
# IT8665: modprobe it87 force_id=0x8665
# IT8686: modprobe it87 force_id=0x8686

# Verify which hwmon device is your chip
for d in /sys/class/hwmon/hwmon*; do echo "$d: $(cat $d/name 2>/dev/null)"; done

# Check PWM channels available
ls /sys/class/hwmon/hwmon8/pwm* 2>/dev/null
```

If your fan is on a different PWM channel than `pwm3`, update `PWM_CHANNEL` in `fan.py` and `truefan_agent.py` accordingly.

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
| Cool | PWM = T + 152 | 1200 rpm | ~36°C |

HDD override: `PWM = 15 × HDD_avg − 500` — applied if higher than the CPU-based value.

### Emergency protection

If any sensor breaches its emergency threshold, PWM is set to **255 immediately**, with absolute priority over all profile formulas and the schedule. HDDs are evaluated individually — a single disk at 55°C triggers the emergency regardless of the others.

| Sensor | Emergency threshold |
|--------|-------------------|
| CPU | 80°C |
| HDD (each disk individually) | 55°C |
| NVMe | 70°C |

The control loop runs every 30 seconds.

### Temperature alert thresholds

Email alerts are sent as early warnings, below the emergency thresholds:

| Sensor | Alert | Recovery |
|--------|-------|----------|
| CPU | 80°C | 75°C |
| HDD | 50°C | 45°C |
| NVMe | 65°C | 60°C |

Alerts fire once when the threshold is crossed. A recovery notification is sent when the temperature drops 5°C below the threshold (hysteresis).

## Web Interface

| Section | Description |
|---------|-------------|
| Status | CPU load bar with Now / 5 min. / 15 min. trend markers, current PWM |
| Fan Profiles | Silent, Balanced, Cool — active profile highlighted in green |
| PWM Manual Control | Slider (0–255) with Apply; Auto disables schedule and returns to hardware control |
| Fan Schedule | 7×24 grid — assign a profile per day and hour. Blocks manual profile changes while active |
| Fan Speeds | Live RPM list and historical graph |
| Temperatures | All sensor readings and three-line graph (CPU, HDD avg., NVMe avg.) |
| Download Logs | Export full log or configurable CSV (select columns) |
| Send Status Email | On-demand email with current temperatures, fan speed and active profile |
| Dark mode | Toggle via moon icon, persisted in browser |

### Fan Schedule behaviour

- When the schedule is **enabled**, the Fan Schedule button turns green and manual profile/PWM changes are blocked
- Clicking **Auto** while schedule is active disables the schedule and returns to hardware automatic control
- The schedule applies silently every 30 seconds via the control loop — no browser connection required
- Saving the schedule from the UI with the toggle **OFF** disables it

## Sensors

| Label | Source | Notes |
|-------|--------|-------|
| CPU | Package id 0 | coretemp |
| Motherboard | temp3 | it8622 |
| HDD 1–4 | temp1 | drivetemp-scsi-0/1/2/3 |
| NVMe 1–2 | Composite | nvme-pci |

## Log format

Each line written by the control loop:

```
2026-05-08 12:00:00.123 - Profile:balanced | CPU:45.0 | HDD:39.2 | NVMe:29.5 | RPM:1100 | PWM:138
```

The log rotates automatically when it reaches 32,768 lines — the oldest half is discarded.

## Known Limitations

- The IT8622 driver occasionally reports 0 RPM glitches. The dashboard filters single-cycle zero readings; three consecutive zero readings are treated as a real fan stop.
- The mail webhook must not run as root. If started from a root context it will fail silently — always start it as a regular user as described in the installation steps.
- After activating PWM Manual Control, returning to hardware automatic control via the Auto button may not work in all cases. If the fan does not respond, a container or system restart may be required to restore automatic control.
