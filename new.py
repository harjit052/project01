from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
ADMIN_PASSWORD = os.environ.get("OUTPASS_ADMIN_PASSWORD", "admin123")
admin_sessions = set()

outings = []


def send_json(handler, payload, status=200):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def format_date(value):
    parsed = datetime.strptime(str(value), "%Y-%m-%d")
    return parsed.strftime("%d/%m/%Y")


def format_time(value):
    hour_text, minute = value.split(":")
    hour = int(hour_text)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour:02d}:{minute} {suffix}"


def has_admin_session(handler):
    cookie = handler.headers.get("Cookie", "")
    token = next((part.split("=", 1)[1] for part in cookie.split(";")
                  if part.strip().startswith("outpass_admin_session=")), "")
    return token in admin_sessions


class StudentPortalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/admin-session":
            send_json(self, {"authenticated": has_admin_session(self)})
            return
        if path == "/api/outings":
            send_json(self, {"outings": outings})
            return

        if path in ("/", "/new.html"):
            html = (BASE_DIR / "new.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if path == "/sist-logo.jpg":
            image = (BASE_DIR / "sist-logo.jpg").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return

        if path == "/profile-photo.jpeg":
            image = (BASE_DIR / "profile-photo.jpeg").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
            return

        self.send_error(404, "Not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/admin-login":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                send_json(self, {"error": "Invalid login request."}, 400)
                return
            if not secrets.compare_digest(str(body.get("password", "")), ADMIN_PASSWORD):
                send_json(self, {"error": "Incorrect admin password."}, 401)
                return
            token = secrets.token_urlsafe(32)
            admin_sessions.add(token)
            data = json.dumps({"authenticated": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", f"outpass_admin_session={token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/admin-logout":
            cookie = self.headers.get("Cookie", "")
            token = next((part.split("=", 1)[1] for part in cookie.split(";")
                          if part.strip().startswith("outpass_admin_session=")), "")
            admin_sessions.discard(token)
            data = json.dumps({"authenticated": False}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", "outpass_admin_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            self.end_headers()
            self.wfile.write(data)
            return
        if path != "/api/gate-pass":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            required = ["passType", "outDate", "outTime", "inDate", "inTime", "purpose"]
            if any(not str(body.get(key, "")).strip() for key in required):
                send_json(self, {"error": "All fields are required."}, 400)
                return

            outing = {
                "id": max((item["id"] for item in outings), default=0) + 1,
                "outDate": format_date(body["outDate"]),
                "outTime": format_time(body["outTime"]),
                "inDate": format_date(body["inDate"]),
                "inTime": format_time(body["inTime"]),
                "purpose": body["purpose"].strip(),
                "status": "Pending",
                "passType": body["passType"].strip(),
                "studentName": body.get("studentName", "Requester").strip() or "Requester",
                "submitted": "Just now",
            }
            outings.insert(0, outing)
            send_json(self, {"outing": outing}, 201)
        except (json.JSONDecodeError, ValueError, KeyError):
            send_json(self, {"error": "Invalid gate pass data."}, 400)

    def do_PATCH(self):
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "outings"]:
            self.send_error(404, "Not found")
            return
        if not has_admin_session(self):
            send_json(self, {"error": "Admin login required."}, 401)
            return
        try:
            outing_id = int(parts[2])
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            status = body.get("status")
            if status not in ("Approved", "Rejected"):
                send_json(self, {"error": "Status must be Approved or Rejected."}, 400)
                return
            outing = next((item for item in outings if item["id"] == outing_id), None)
            if outing is None:
                send_json(self, {"error": "Request not found."}, 404)
                return
            outing["status"] = status
            send_json(self, {"outing": outing})
        except (json.JSONDecodeError, ValueError):
            send_json(self, {"error": "Invalid request."}, 400)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), StudentPortalHandler)
    print(f"Serving outpass request system at http://0.0.0.0:{PORT}")
    server.serve_forever()
