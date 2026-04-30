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
    try:
        with open(f"{fanlib.HWMON_PATH}/pwm{fanlib.PWM_CHANNEL}_enable", "w") as f:
            f.write("2")
    except Exception as e:
        print(f"Error setting PWM auto: {e}")


def get_uptime():
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


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
    fanlib.set_profile("manual")
    fanlib.set_pwm_value(int(value))
    return jsonify({'status': 'ok'})


@app.route('/set/<profile>', methods=['POST'])
@auth_required
def set_profile(profile):
    fanlib.set_profile(profile)
    fanlib.control()
    return jsonify({'status': 'ok', 'profile': profile})


@app.route('/auto', methods=['POST'])
@auth_required
def set_auto():
    pwm_auto()
    fanlib.set_profile("manual")
    return jsonify({'status': 'ok'})


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
    return jsonify({
        'profile': profile,
        'uptime': get_uptime(),
        'load': get_cpu_load()
    })


start_control_loop()
fanlib.control()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
