"""An OpenAI-shaped echo server so LiteLLM has an upstream with no key and no spend.
POST /v1/chat/completions -> a fixed completion. GET /health -> ok. Nothing else."""
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(200, {"status": "ok"}) if self.path in ("/health", "/v1/models") else self._send(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        self._send(200, {"id": "fx", "object": "chat.completion", "created": int(time.time()), "model": "fx-echo",
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": "pong"}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    def log_message(self, *a):
        pass

ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
