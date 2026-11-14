"""Experimental Python-powered paper trading terminal. No broker or live feed."""
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from simulator import Simulator, Risk
import robot_engine

ROOT = Path(__file__).resolve().parent
sessions = {}
lock = threading.RLock()
MAX_BODY = 8192

def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must have timezone")
    return dt.astimezone(timezone.utc)

def snapshot(s, price=None):
    return {"symbol": s.symbol, "realized": s.realized, "trades": s.trades,
            "locked": s.locked, "position": s.position, "history": s.history,
            "unrealized": s.unrealized(price) if price is not None and s.position else 0.0,
            "max_daily_loss": s.risk.max_daily_loss, "stop_brl": s.risk.stop_brl,
            "max_trades": s.risk.max_trades}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/robots":
            with robot_engine.GUARD:
                return self.send_json(200, robot_engine.public(robot_engine.load()))
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "live_feed": False, "real_orders": False})
        if path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown endpoint"})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path == "/api/robots":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY: return self.send_json(413, {"error": "Invalid request size"})
                data = json.loads(self.rfile.read(length))
                return self.send_json(200, robot_engine.command(data.get("action"), data.get("robot"), data.get("settings")))
            except (ValueError, KeyError, TypeError) as exc:
                return self.send_json(400, {"error": str(exc)})
        if urlparse(self.path).path != "/api/paper":
            return self.send_json(404, {"error": "Unknown endpoint"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.send_json(413, {"error": "Invalid request size"})
            data = json.loads(self.rfile.read(length))
            action = data["action"]
            with lock:
                if action == "create":
                    risk = Risk(**data["risk"])
                    sim = Simulator(data["symbol"], risk)
                    sid = secrets.token_urlsafe(24)
                    sessions[sid] = sim
                    return self.send_json(200, {"session": sid, "state": snapshot(sim)})
                sid = data["session"]
                sim = sessions.get(sid)
                if sim is None:
                    return self.send_json(404, {"error": "Session not found; create a new one"})
                if action == "state":
                    result = None
                else:
                    price = float(data["price"])
                    timestamp = parse_time(data["timestamp"])
                    if action == "open":
                        result = sim.open(data["side"], int(data["contracts"]), price, timestamp)
                    elif action == "close":
                        result = sim.close(price, timestamp)
                    elif action == "mark":
                        result = sim.mark(price, timestamp)
                    elif action == "emergency":
                        result = sim.emergency_stop(price, timestamp)
                    else:
                        raise ValueError("Unknown action")
                return self.send_json(200, {"state": snapshot(sim, price if action != "state" else None), "result": result})
        except (ValueError, KeyError, TypeError, RuntimeError, OverflowError) as exc:
            return self.send_json(400, {"error": str(exc)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    robot_engine.start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
