"""Collect only non-secret MinIO audit fields for range-read evidence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal


def stop(signum, frame):
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        for line in payload.splitlines():
            event = json.loads(line)
            request = {k.lower(): v for k, v in event.get("requestHeader", {}).items()}
            response = {k.lower(): v for k, v in event.get("responseHeader", {}).items()}
            print(json.dumps({
                "time": event.get("time"),
                "api": event.get("api"),
                "user_agent": event.get("userAgent"),
                "range": request.get("range"),
                "content_range": response.get("content-range"),
                "content_length": response.get("content-length"),
            }), flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
