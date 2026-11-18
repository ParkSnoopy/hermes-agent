"""Shared signed-object transport fixture for source-channel administration and reads."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit
import xml.sax.saxutils


ARCHIVED_IDENTITY = {
    "token": "a" * 16, "displayName": "Archive fixture", "appNamePascal": "Fixture",
    "artifactNamePascal": "Fixture", "appId": "ai.fixture.archive",
    "msixAppIdWithOrg": "Fixture.Archive", "cliName": "fixture-archive",
    "windowsExecutableName": "Fixture",
}


@contextmanager
def object_server():
    objects, headers, requests = {}, {}, []
    lock = Lock()
    faults = {"lose_put": False, "stale_public": None, "conflict": None, "bad_pagination": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            with lock:
                requests.append(("GET", self.path))
                if "list-type" in query:
                    prefix = query.get("prefix", [""])[0]
                    keys = sorted(k for k in objects if k.startswith(prefix))
                    start = int(query.get("continuation-token", ["0"])[0])
                    page = keys[start:start + 2]
                    more = start + 2 < len(keys)
                    body = ("<ListBucketResult>" + "".join(
                        f"<Contents><Key>{xml.sax.saxutils.escape(k)}</Key></Contents>" for k in page)
                        + f"<IsTruncated>{str(more).lower()}</IsTruncated>"
                        + (f"<NextContinuationToken>{start + 2}</NextContinuationToken>" if more else "")
                        + "</ListBucketResult>").encode()
                    if faults["bad_pagination"]:
                        body = b"<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>"
                    etag = None
                else:
                    key = parsed.path.removeprefix("/bucket/")
                    body = objects.get(key)
                    if not self.headers.get("Authorization") and faults["stale_public"] is not None:
                        body = faults["stale_public"]
                    etag = '"' + hashlib.sha256(body).hexdigest() + '"' if body is not None else None
                self.send_response(200 if body is not None else 404)
                if etag:
                    self.send_header("ETag", etag)
                self.end_headers()
                if body is not None:
                    self.wfile.write(body)

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            key = self.path.removeprefix("/bucket/")
            with lock:
                requests.append(("PUT", key))
                if faults["conflict"]:
                    conflict = faults["conflict"]
                    faults["conflict"] = None
                    conflict(objects, key)
                old = objects.get(key)
                etag = '"' + hashlib.sha256(old).hexdigest() + '"' if old is not None else None
                conflict = ((self.headers.get("If-None-Match") == "*" and old is not None)
                            or (self.headers.get("If-Match") is not None and self.headers["If-Match"] != etag))
                if conflict:
                    self.send_response(412)
                    self.end_headers()
                    return
                objects[key] = body
                headers[key] = dict(self.headers)
                if faults["lose_put"]:
                    faults["lose_put"] = False
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(200)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", objects, headers, requests, faults
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def publisher(url, **kwargs):
    from scripts.releases.channels import ChannelPublisher, R2ChannelStore
    store = R2ChannelStore({"access_key_id": "fixture", "secret_key": "fixture"}, url, "bucket")
    return ChannelPublisher(store, "example/hermes-agent", url + "/bucket",
                            authorize=kwargs.pop("authorize", lambda action, record: None), **kwargs)


def source_record(name="main"):
    return {"schema": 1, "name": name, "repository": "example/hermes-agent",
            "policy": "source-branch", "state": "active", "revision": 1,
            "nextSequence": 1, "identity": None, "head": None,
            "delivery": {"kind": "source-branch", "branch": "main"}}
