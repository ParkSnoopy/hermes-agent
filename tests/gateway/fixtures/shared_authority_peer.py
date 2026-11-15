"""Loopback model peer shared by gateway and local-surface tests."""

from http.server import BaseHTTPRequestHandler
import json


class ModelPeer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "local-wire-stub", "object": "model"}]}).encode())

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = next((m.get("content", "") for m in reversed(request.get("messages", []))
                     if m.get("role") == "user"), "")
        marker = "WS_SHARED" if "WS_SHARED" in json.dumps(last) else "MESSAGING_WARM"
        reply = f"LOCAL_ACK_{marker}"
        if 'BLOCK_FIFO' in json.dumps(last):
            self.server.blocked.set()
            assert self.server.release.wait(15), 'fixture model gate timed out'
        self.server.requests.append(request) if 'messages' in request else self.server.metadata_requests.append(request)
        base = {"id": "chatcmpl-local", "model": "local-wire-stub", "created": 1}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if request.get("stream") else "application/json")
        self.end_headers()
        if request.get("stream"):
            for delta, finish in [({"role": "assistant", "content": reply}, None), ({}, "stop")]:
                chunk = {**base, "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(json.dumps({**base, "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode())
