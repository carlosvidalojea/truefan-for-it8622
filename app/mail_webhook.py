#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import os

PORT = 5003
API_KEY_FILE = "/mnt/nas/apps/truefan/truenas_api_key.env"

def get_api_key():
    with open(API_KEY_FILE) as f:
        for line in f:
            if line.startswith("TRUENAS_API_KEY="):
                return line.strip().split("=", 1)[1].strip('"')
    return None

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/send":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            subject = body.get("subject", "TrueFan Alert")
            text = body.get("text", "")
            try:
                payload = json.dumps({"subject": subject, "text": text})
                subprocess.call(["/usr/bin/midclt", "call", "mail.send", payload])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                print(f"Email sent: {subject}", flush=True)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
                print(f"Error: {e}", flush=True)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default logging

print(f"Mail webhook listening on port {PORT}", flush=True)
HTTPServer(("0.0.0.0", PORT), WebhookHandler).serve_forever()
