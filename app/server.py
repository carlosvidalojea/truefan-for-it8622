from flask import Flask, jsonify, send_from_directory, request, Response
import subprocess
import os
import threading
import time
import sys
import hashlib

sys.path.insert(0, '/app')
import fan as fanlib

app = Flask(__name__, static_folder="static", template_folder="templates")

AUTH_USER = "admin"
AUTH_PASS = "truefan"


def check_auth(username, password):
    return username == AUTH_USER and password == AUTH_PASS


def require_auth():
    return Response(
        "Acceso restringido. Introduce tus credenciales.",
        401,
        {"WWW-Authenticate": 'Basic realm="TrueFan"'}
    )


def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return require_auth()
        return f(*args, **kwargs)
    return decorated

_control_running = False


def control_loop():
    while _control_running:
        try:
            fanlib.control()
        except Exception as e:
            print(f"Control loop error: {e}")
        time.sleep(30)


def start_control_loop():
    global _control_running
    _control_running = True
    t = threading.Thread(target=control_loop, daemon=True)
    t.start()


def pwm_auto():
    fanlib._call_agent(fanlib.PWM_AGENT_URL, "/pwm/auto", {})


def get_cpu_load():
    load1, load5, load15 = os.getloadavg()
    return {
        '1min': round(load1, 2),
        '5min': round(load5, 2),
        '15min': round(load15, 2),
    }


@app.route('/')
@auth_required
def index():
    return send_from_directory(app.template_folder, 'index.html')


@app.route('/sensors')
@auth_required
def sensors():
    output = fanlib.read_sensors_output()
    temps_raw, fans = fanlib.parse_all_sensors(output)
    ORDER = ["CPU", "Motherboard", "HDD 1", "HDD 2", "HDD 3", "HDD 4", "NVMe 1", "NVMe 2"]
    temps = {}
    for key in ORDER:
        if key in temps_raw:
            temps[key] = fanlib.format_temp(temps_raw[key])
    return jsonify({'fans': fans, 'temps': temps})


@app.route('/pwm/<value>', methods=['POST'])
@auth_required
def set_pwm(value):
    sched = fanlib.load_schedule()
    if sched.get("enabled"):
        return jsonify({'status': 'blocked', 'reason': 'schedule_active'}), 403
    fanlib.set_profile("manual")
    fanlib.set_pwm_value(int(value))
    return jsonify({'status': 'ok'})


@app.route('/set/<profile>', methods=['POST'])
@auth_required
def set_profile(profile):
    sched = fanlib.load_schedule()
    if sched.get("enabled"):
        return jsonify({'status': 'blocked', 'reason': 'schedule_active'}), 403
    fanlib.set_profile(profile)
    fanlib.control()
    return jsonify({'status': 'ok', 'profile': profile})


@app.route('/auto', methods=['POST'])
@auth_required
def set_auto():
    # Disable schedule if active, then return to hardware auto
    sched = fanlib.load_schedule()
    if sched.get("enabled"):
        sched["enabled"] = False
        fanlib.save_schedule(sched)
    pwm_auto()
    fanlib.set_profile("manual")
    return jsonify({'status': 'ok', 'schedule_disabled': True})


@app.route('/send-status', methods=['POST'])
@auth_required
def send_status_email():
    output = fanlib.read_sensors_output()
    temps_raw, fans = fanlib.parse_all_sensors(output)
    profile = fanlib.load_profile()
    pwm = fanlib.read_current_pwm()

    ORDER = ["CPU", "Motherboard", "HDD 1", "HDD 2", "HDD 3", "HDD 4", "NVMe 1", "NVMe 2"]
    temp_lines = "\n".join(f"  {k}: {fanlib.format_temp(temps_raw[k])}" for k in ORDER if k in temps_raw)
    fan_lines = "\n".join(f"  {k}: {v}" for k, v in fans.items())

    text = (
        f"TrueFan Status Report\n"
        f"{'='*30}\n\n"
        f"Profile: {profile}\n"
        f"PWM: {pwm}\n\n"
        f"Temperatures:\n{temp_lines}\n\n"
        f"Fan Speeds:\n{fan_lines}\n"
    )
    fanlib.send_alert("TrueFan: Status Report", text)
    return jsonify({'status': 'ok'})


@app.route('/status')
@auth_required
def status():
    profile = fanlib.load_profile()
    pwm = fanlib.read_current_pwm()
    emergency = fanlib._emergency_active
    return jsonify({
        'profile': profile,
        'load': get_cpu_load(),
        'pwm': pwm,
        'emergency': emergency,
        'chip': fanlib.get_chip_name(),
    })



@app.route('/schedule', methods=['GET'])
@auth_required
def get_schedule():
    return jsonify(fanlib.load_schedule())


@app.route('/schedule', methods=['POST'])
@auth_required
def set_schedule():
    data = request.get_json()
    fanlib.save_schedule(data)
    return jsonify({'status': 'ok'})


@app.route('/log/lines')
@auth_required
def log_lines():
    try:
        with open(fanlib.LOG_FILE, 'r') as f:
            lines = f.readlines()
        return jsonify({'lines': len(lines), 'max': fanlib.LOG_MAX_LINES})
    except FileNotFoundError:
        return jsonify({'lines': 0, 'max': fanlib.LOG_MAX_LINES})


@app.route('/log/download')
@auth_required
def log_download():
    """Download full log as plain text."""
    try:
        with open(fanlib.LOG_FILE, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    from flask import make_response
    resp = make_response(content)
    resp.headers['Content-Type'] = 'text/plain'
    resp.headers['Content-Disposition'] = 'attachment; filename="truefan.log"'
    return resp


@app.route('/log/csv')
@auth_required
def log_csv():
    """Convert log to CSV with selected columns."""
    cols = request.args.get('cols', 'timestamp,cpu,hdd,nvme,rpm,pwm,profile')
    col_list = [c.strip() for c in cols.split(',')]
    rows = [';'.join(col_list)]
    try:
        with open(fanlib.LOG_FILE, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    import re as _re
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Format: 2026-05-03 12:00:00 - Profile:balanced | CPU:45.0 | HDD:39.0 | NVMe:28.5 | RPM:1100 | PWM:138
        ts_m = _re.match(r'^([\d\-: .]+) - ', line)
        ts = ts_m.group(1).strip() if ts_m else ''
        def extract(key):
            m = _re.search(rf'{key}:([^\s|]+)', line)
            return m.group(1) if m else ''
        mapping = {
            'timestamp': ts,
            'cpu':     extract('CPU'),
            'hdd':     extract('HDD'),
            'nvme':    extract('NVMe'),
            'rpm':     extract('RPM'),
            'pwm':     extract('PWM'),
            'profile': extract('Profile'),
        }
        rows.append(';'.join(mapping.get(c, '') for c in col_list))
    from flask import make_response
    resp = make_response('\n'.join(rows))
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename="truefan_log.csv"'
    return resp


start_control_loop()
fanlib.control()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
