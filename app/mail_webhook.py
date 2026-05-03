#!/usr/bin/env python3
"""
TrueFan Mail Webhook — must run as a regular TrueNAS user (not root).
Receives HTTP POST requests from the container and sends emails via midclt.
Listens on port 5004.

Start manually:
    nohup python3 /path/to/mail_webhook.py &
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess

PORT = 5004


class MailHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/mail":
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
        pass


print(f"Mail webhook listening on port {PORT}", flush=True)
HTTPServer(("0.0.0.0", PORT), MailHandler).serve_forever()
