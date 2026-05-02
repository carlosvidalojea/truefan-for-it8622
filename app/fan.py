import os
import re
import subprocess
from datetime import datetime

HWMON_PATH = "/sys/class/hwmon/hwmon8"
PROFILE_FILE = "/app/fan_profile.conf"
LOG_FILE = "/app/logs/fan.log"
PWM_CHANNEL = 3

PWM_MIN = 50
PWM_MAX = 250

# Emergency thresholds — absolute priority, any breach → PWM 255
EMERGENCY_CPU  = 80   # °C
EMERGENCY_HDD  = 55   # °C — permanent damage risk above this
EMERGENCY_NVME = 70   # °C

# Email alert thresholds (early warning, below emergency)
ALERT_THRESHOLDS = {
    "CPU":  80,
    "HDD":  50,
    "NVMe": 65,
}
ALERT_HYSTERESIS = 5
_alert_active = set()


MAIL_WEBHOOK = "http://host-gateway:5003/send"


def send_alert(subject, text):
    try:
        import urllib.request
        import json as _json
        data = _json.dumps({"subject": subject, "text": text}).encode()
        req = urllib.request.Request(MAIL_WEBHOOK, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        print(f"Alert sent: {subject}", flush=True)
    except Exception as e:
        print(f"Alert error: {e}", flush=True)


def check_temp_alerts(temps):
    global _alert_active
    triggered = {}
    recovered = set()

    for label, val in temps.items():
        category = "CPU" if label == "CPU" else "HDD" if label.startswith("HDD") else "NVMe" if label.startswith("NVMe") else None
        if category is None:
            continue
        threshold = ALERT_THRESHOLDS[category]
        if val >= threshold:
            triggered[label] = (val, threshold)
        elif label in _alert_active and val <= threshold - ALERT_HYSTERESIS:
            recovered.add(label)

    new_alerts = {k: v for k, v in triggered.items() if k not in _alert_active}
    if new_alerts:
        lines = [f"  {label}: {val}°C (threshold: {thr}°C)" for label, (val, thr) in new_alerts.items()]
        send_alert(
            "TrueFan: High temperature alert",
            "The following sensors have exceeded their temperature threshold:\n\n" + "\n".join(lines)
        )
        _alert_active.update(new_alerts.keys())

    if recovered:
        lines = [f"  {label}: {temps[label]:.0f}°C" for label in recovered]
        send_alert(
            "TrueFan: Temperature back to normal",
            "The following sensors are back below their threshold:\n\n" + "\n".join(lines)
        )
        _alert_active -= recovered


def parse_temp(s):
    m = re.search(r'([+-]?\d+(?:\.\d+)?)\s*°?\s*C', s)
    if m:
        return float(m.group(1))
    return None


def read_sensors_output():
    try:
        return subprocess.check_output(["sensors"], encoding="utf-8", stderr=subprocess.DEVNULL)
    except:
        return ""


def parse_all_sensors(output):
    temps = {}
    fans = {}
    hdd_scsi = {}
    nvme_list = []
    nvme_index = 0
    current_adapter = None

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if ':' not in stripped and stripped.count('-') >= 2 and stripped[0].isalpha():
            if stripped.startswith('it8622'):
                current_adapter = 'it8622'
            elif stripped.startswith('coretemp'):
                current_adapter = 'coretemp'
            elif stripped.startswith('drivetemp'):
                m = re.search(r'scsi-(\d+)', stripped)
                current_adapter = ('drivetemp', int(m.group(1)) if m else 99)
            elif stripped.startswith('nvme-pci'):
                current_adapter = ('nvme', nvme_index)
                nvme_index += 1
            else:
                current_adapter = None
            continue

        if stripped.startswith('Adapter:'):
            continue

        if current_adapter == 'coretemp':
            if stripped.startswith('Package id 0'):
                t = parse_temp(line.split(':', 1)[1] if ':' in line else line)
                if t is not None:
                    temps['CPU'] = t

        elif current_adapter == 'it8622':
            if stripped.startswith('temp3'):
                t = parse_temp(line.split(':', 1)[1] if ':' in line else line)
                if t is not None:
                    temps['Motherboard'] = t
            m = re.match(r'(fan\d+):\s+(\d+)\s+RPM', stripped)
            if m:
                rpm = int(m.group(2))
                if rpm > 0:
                    fans[m.group(1)] = f"{rpm} RPM"

        elif isinstance(current_adapter, tuple) and current_adapter[0] == 'drivetemp':
            if stripped.startswith('temp1'):
                t = parse_temp(line.split(':', 1)[1] if ':' in line else line)
                if t is not None:
                    hdd_scsi[current_adapter[1]] = t

        elif isinstance(current_adapter, tuple) and current_adapter[0] == 'nvme':
            if stripped.startswith('Composite'):
                t = parse_temp(line.split(':', 1)[1] if ':' in line else line)
                if t is not None:
                    nvme_list.append((current_adapter[1], t))

    for hdd_num, scsi_idx in enumerate(sorted(hdd_scsi.keys()), 1):
        temps[f'HDD {hdd_num}'] = hdd_scsi[scsi_idx]

    for nvme_num, (_, t) in enumerate(sorted(nvme_list, key=lambda x: x[0]), 1):
        temps[f'NVMe {nvme_num}'] = t

    return temps, fans


def format_temp(t):
    return f"{t:.0f}°C"


def is_emergency(cpu, hdds, nvmes):
    """
    Returns True if any sensor breaches its emergency threshold.
    HDDs are checked individually — permanent damage occurs before CPU thermal throttling.
    This check has absolute priority over all profile formulas.
    """
    if cpu >= EMERGENCY_CPU:
        return True
    if any(t >= EMERGENCY_HDD for t in hdds):
        return True
    if any(t >= EMERGENCY_NVME for t in nvmes):
        return True
    return False


def calculate_pwm(profile_name, cpu_temp, hdd_avg):
    if profile_name == "silent":
        pwm_cpu = 3 * cpu_temp - 51
    elif profile_name == "balanced":
        pwm_cpu = 2 * cpu_temp + 48
    elif profile_name == "performance":
        pwm_cpu = cpu_temp + 152
    else:
        pwm_cpu = 2 * cpu_temp + 48

    pwm_hdd = 15 * hdd_avg - 500 if hdd_avg is not None else 0

    pwm = max(pwm_cpu, pwm_hdd)
    pwm = max(PWM_MIN, min(PWM_MAX, int(pwm)))
    return pwm


def load_profile():
    if not os.path.exists(PROFILE_FILE):
        return "balanced"
    with open(PROFILE_FILE) as f:
        for line in f:
            if line.startswith("profile="):
                return line.strip().split("=")[1]
    return "balanced"


def read_current_pwm():
    try:
        with open(f"{HWMON_PATH}/pwm{PWM_CHANNEL}", "r") as f:
            return int(f.read().strip())
    except:
        return 125


def set_pwm_value(pwm):
    pwm = max(0, min(255, int(pwm)))
    try:
        with open(f"{HWMON_PATH}/pwm{PWM_CHANNEL}_enable", "w") as f:
            f.write("1")
        with open(f"{HWMON_PATH}/pwm{PWM_CHANNEL}", "w") as f:
            f.write(str(pwm))
    except Exception as e:
        print(f"Error setting PWM: {e}")


def log_status(cpu, hdd_avg, pwm, profile):
    os.makedirs("/app/logs", exist_ok=True)
    with open(LOG_FILE, "a") as log:
        log.write(f"{datetime.now()} - Profile: {profile} | CPU:{cpu}°C HDD_avg:{hdd_avg}°C | PWM:{pwm}\n")


def set_profile(name):
    with open(PROFILE_FILE, "w") as f:
        f.write(f"profile={name}\n")
    print(f"Profile set to: {name}")


def get_profile():
    print(f"Active profile: {load_profile()}")


def control():
    profile_name = load_profile()
    if profile_name == "manual":
        return

    output = read_sensors_output()
    temps, _ = parse_all_sensors(output)

    cpu = temps.get("CPU", 0)
    hdds = [v for k, v in temps.items() if k.startswith("HDD")]
    nvmes = [v for k, v in temps.items() if k.startswith("NVMe")]
    hdd_avg = round(sum(hdds) / len(hdds), 1) if hdds else None

    # Emergency check — absolute priority over all formulas
    if is_emergency(cpu, hdds, nvmes):
        new_pwm = 255
        print(f"EMERGENCY: CPU:{cpu}°C HDDs:{hdds} NVMes:{nvmes} → PWM:255")
    else:
        new_pwm = calculate_pwm(profile_name, cpu, hdd_avg)

    set_pwm_value(new_pwm)
    log_status(cpu, hdd_avg, new_pwm, profile_name)
    print(f"Profile '{profile_name}' | CPU:{cpu}°C HDD_avg:{hdd_avg}°C | PWM:{new_pwm}")
    check_temp_alerts(temps)


def status():
    output = read_sensors_output()
    temps, _ = parse_all_sensors(output)
    ORDER = ["CPU", "Motherboard", "HDD 1", "HDD 2", "HDD 3", "HDD 4", "NVMe 1", "NVMe 2"]
    for key in ORDER:
        if key in temps:
            print(f"{key}: {format_temp(temps[key])}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: fan.py [status|control|set-profile <name>|get-profile|set <pwm>]")
        exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        status()
    elif cmd == "control":
        control()
    elif cmd == "set-profile" and len(sys.argv) == 3:
        set_profile(sys.argv[2])
    elif cmd == "get-profile":
        get_profile()
    elif cmd == "set" and len(sys.argv) == 3:
        set_pwm_value(int(sys.argv[2]))
    else:
        print("Unknown command.")
