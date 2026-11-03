"""End-to-end test of the running stack, through the frontend's nginx like a browser would.

    upload -> Celery worker extracts, chunks, embeds (local model) -> pgvector
    query  -> retrieval (RLS-scoped) -> generation (stub LLM) -> answer + citations
    stream -> same, as Server-Sent Events
    a second tenant can neither query the first tenant's collection nor see its data

Standard library only. Usage: python e2e/run_e2e.py [--base http://localhost:3001]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

SECRET = "ZEBRA-7731"
DOC = (
    "Acme Rocketry employee handbook.\n\n"
    "Section 4: Launch procedures. The launch code for the spring test flight is "
    f"{SECRET}. Only the flight director may read it aloud.\n\n"
    "Section 5: Cafeteria hours are 8am to 3pm on weekdays.\n"
)


def call(base, method, path, token=None, body=None, data=None, headers=None, timeout=300):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def ok(base, method, path, **kw):
    status, text = call(base, method, path, **kw)
    if status >= 300:
        sys.exit(f"FAIL {method} {path} -> {status}: {text[:500]}")
    return json.loads(text) if text and text[0] in "[{" else text


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = b""
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
    for name, (filename, content, ctype) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def step(msg):
    print(f"--> {msg}", flush=True)


def wait_for(base, deadline_s=300):
    step("waiting for the stack (nginx -> backend health)")
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            status, _ = call(base, "GET", "/api/v1/health", timeout=5)
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(3)
    sys.exit("FAIL backend never became healthy")


def register(base, label):
    suffix = uuid.uuid4().hex[:8]
    tokens = ok(base, "POST", "/api/v1/auth/register", body={
        "email": f"{label}-{suffix}@example.com", "password": "e2e-password-123",
        "full_name": label, "org_name": f"{label} org {suffix}",
    })
    return tokens["access_token"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:3001")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    wait_for(base)

    step("frontend serves the app and falls back to index.html for client routes")
    for path in ("/", "/chat"):
        html = ok(base, "GET", path)
        assert 'id="root"' in html, f"{path} did not return the SPA shell"

    step("register two tenants")
    alice, bob = register(base, "alice"), register(base, "bob")

    step("alice creates a collection and uploads the handbook")
    coll = ok(base, "POST", "/api/v1/collections", token=alice, body={"name": "Handbook"})
    data, headers = multipart({"collection_id": coll["id"]},
                              {"files": ("handbook.txt", DOC.encode(), "text/plain")})
    doc_id = ok(base, "POST", "/api/v1/documents/upload", token=alice, data=data, headers=headers)[0]["id"]

    step("worker ingests it (first run downloads the embedding model)")
    end = time.time() + 600
    while True:
        doc = ok(base, "GET", f"/api/v1/documents/{doc_id}", token=alice)
        if doc["status"] == "completed":
            break
        if doc["status"] == "failed":
            sys.exit(f"FAIL ingestion failed: {doc.get('error_message')}")
        if time.time() > end:
            sys.exit(f"FAIL ingestion stuck in {doc['status']!r}")
        time.sleep(3)
    assert doc["chunk_count"] >= 1, doc
    print(f"    {doc['chunk_count']} chunk(s)")

    question = "What is the launch code for the spring test flight?"

    step("alice queries: retrieval finds the chunk, the LLM receives it, citations point at it")
    res = ok(base, "POST", "/api/v1/query", token=alice, body={"query": question, "collection_ids": [coll["id"]]})
    assert SECRET in res["answer"], f"context did not reach the LLM: {res['answer']!r}"
    assert res["citations"], "no citations"
    assert res["citations"][0]["document_name"] == "handbook.txt", res["citations"][0]
    assert SECRET in res["citations"][0]["content"]
    print(f"    answer: {res['answer']}")

    step("alice streams the same query over SSE")
    status, text = call(base, "POST", "/api/v1/query/stream", token=alice,
                        body={"query": question, "collection_ids": [coll["id"]]})
    assert status == 200, (status, text[:300])
    events = [json.loads(line[6:]) for line in text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    streamed = "".join(e.get("content", "") for e in events if e.get("type") == "token")
    assert SECRET in streamed, f"stream answer: {streamed!r}"
    assert any(e.get("type") == "citations" and e.get("citations") for e in events), "no citations event"
    assert text.rstrip().endswith("data: [DONE]")

    step("bob cannot query alice's collection")
    status, _ = call(base, "POST", "/api/v1/query", token=bob, body={"query": question, "collection_ids": [coll["id"]]})
    assert status in (403, 404), f"cross-tenant query returned {status}"

    step("bob's unscoped query sees none of alice's data (row-level security)")
    res = ok(base, "POST", "/api/v1/query", token=bob, body={"query": question})
    assert SECRET not in res["answer"] and not res["citations"], res

    step("bob cannot read alice's document")
    status, _ = call(base, "GET", f"/api/v1/documents/{doc_id}", token=bob)
    assert status == 404, status

    print("E2E PASSED")


if __name__ == "__main__":
    main()
