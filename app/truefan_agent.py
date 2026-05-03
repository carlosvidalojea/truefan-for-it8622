#!/usr/bin/env python3
"""
TrueFan PWM Agent — runs on the TrueNAS host as root.
Receives HTTP POST requests from the container and writes PWM values to sysfs.
Listens on port 5003.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import glob
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
LOGGER = logging.getLogger("truefan-agent")

PORT = 5003
HWMON_ROOT = "/sys/class/hwmon"
PWM_BASENAME_RE = re.compile(r"^pwm[0-9]+$")
PWM_CHANNEL = 3


def _normalize(path):
    return os.path.realpath(os.path.abspath(path))


def _is_safe_pwm_path(path):
    norm_path = _normalize(path)
    base = os.path.basename(norm_path)
    return (
        PWM_BASENAME_RE.match(base) is not None
        and os.path.isfile(norm_path)
        and "hwmon" in norm_path
    )


def _find_it8x_hwmon():
    try:
        for hwmon in sorted(os.listdir(HWMON_ROOT)):
            name_file = os.path.join(HWMON_ROOT, hwmon, "name")
            try:
                with open(name_file) as f:
                    if f.read().strip().startswith("it8"):
                        return os.path.join(HWMON_ROOT, hwmon)
            except OSError:
                continue
    except Exception:
        pass
    return None


def write_pwm(pwm):
    pwm = max(0, min(255, int(pwm)))
    hwmon_dir = _find_it8x_hwmon()
    target = None
    if hwmon_dir:
        candidate = os.path.join(hwmon_dir, f"pwm{PWM_CHANNEL}")
        if _is_safe_pwm_path(candidate):
            target = _normalize(candidate)
    if target is None:
        pattern = f"{HWMON_ROOT}/hwmon*/pwm[0-9]*"
        files = [_normalize(f) for f in sorted(glob.glob(pattern))
                 if "_enable" not in f and _is_safe_pwm_path(f)]
        target = next(iter(files), None)
    if target is None:
        LOGGER.error("No safe PWM target found")
        return False
    enable_file = f"{target}_enable"
    try:
        if os.path.isfile(enable_file):
            with open(enable_file, "w") as f:
                f.write("1")
    except OSError as e:
        LOGGER.debug("Could not set manual mode: %s", e)
    try:
        with open(target, "w") as f:
            f.write(str(pwm))
        LOGGER.info("PWM %d written to %s", pwm, target)
        return True
    except Exception as e:
        LOGGER.error("Failed writing PWM to %s: %s", target, e)
        return False


def set_pwm_auto():
    hwmon_dir = _find_it8x_hwmon()
    if not hwmon_dir:
        LOGGER.error("IT8x hwmon not found")
        return False
    enable_file = os.path.join(hwmon_dir, f"pwm{PWM_CHANNEL}_enable")
    try:
        with open(enable_file, "w") as f:
            f.write("2")
        LOGGER.info("PWM auto mode set via %s", enable_file)
        return True
    except Exception as e:
        LOGGER.error("Failed setting PWM auto: %s", e)
        return False


class AgentHandler(BaseHTTPRequestHandler):
    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        try:
            if self.path == "/pwm":
                body = self._read_json()
                ok = write_pwm(body.get("value", 128))
                self._respond(200 if ok else 500, {"status": "ok" if ok else "error"})
            elif self.path == "/pwm/auto":
                ok = set_pwm_auto()
                self._respond(200 if ok else 500, {"status": "ok" if ok else "error"})
            else:
                self._respond(404, {"error": "not found"})
        except Exception as e:
            LOGGER.exception("Handler error")
            self._respond(500, {"error": str(e)})

    def log_message(self, format, *args):
        pass


LOGGER.info("TrueFan PWM Agent listening on port %d", PORT)
HTTPServer(("0.0.0.0", PORT), AgentHandler).serve_forever()
