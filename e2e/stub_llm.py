"""A stand-in LLM for the end-to-end test: a tiny OpenAI-compatible /v1/chat/completions server.

The app reaches it through the LLM registry like any client endpoint (LLM_ENDPOINTS in
e2e.env), so CI exercises the real request/stream code paths without an API key or cost.

It answers from what it was sent: if the secret fact from the test document is anywhere in the
prompt, it repeats it — which proves retrieval found the chunk and the generator passed it on.
Standard library only, so it runs in a bare python image.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = "ZEBRA-7731"


def _answer(messages):
    seen = json.dumps(messages)
    if SECRET in seen:
        return f"According to the handbook, the launch code is {SECRET} [Source 1]."
    return "I could not find that in the provided context."


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._json(200, {"status": "ok"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": f"no route {self.path}"}})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        answer = _answer(body.get("messages", []))
        usage = {"prompt_tokens": 100, "completion_tokens": len(answer.split()), "total_tokens": 0}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        base = {"id": "stub-1", "created": int(time.time()), "model": body.get("model", "stub")}

        if not body.get("stream"):
            return self._json(200, {
                **base,
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer}}],
                "usage": usage,
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(chunk):
            self.wfile.write(f"data: {json.dumps({**base, 'object': 'chat.completion.chunk', **chunk})}\n\n".encode())

        send({"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        for word in answer.split(" "):
            send({"choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}]})
        send({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if (body.get("stream_options") or {}).get("include_usage"):
            send({"choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("stub-llm:", fmt % args, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
