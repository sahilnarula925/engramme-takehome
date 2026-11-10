from __future__ import annotations

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .store import Store
from .worker import Workers


def public(memory):
    return {k: v for k, v in memory.items() if not k.startswith("_")}


def validate_event(body):
    for key in ("user_id", "source_id", "item_id"):
        if not isinstance(body.get(key), str) or not re.fullmatch(r"[A-Za-z0-9-]+", body[key]):
            raise ValueError(f"{key} must contain letters, digits or hyphens")
    if type(body.get("version")) is not int or not 0 < body["version"] < 2**63:
        raise ValueError("version must be a positive 64-bit integer")
    if body.get("kind") not in ("upsert", "delete"):
        raise ValueError("kind must be upsert or delete")
    if body["kind"] == "upsert" and not isinstance(body.get("content"), str):
        raise ValueError("upsert requires string content")
    if not isinstance(body.get("occurred_at"), str):
        raise ValueError("occurred_at is required")


class Handler(BaseHTTPRequestHandler):
    store: Store

    def log_message(self, *args):
        pass

    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        url = urlsplit(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        query = parse_qs(url.query)
        if self.command == "GET" and parts == ["v1", "health"]:
            with self.store.pool.connection() as conn:
                conn.execute("SELECT 1")
            return self.send(200, {"ok": True})
        if self.command == "POST" and parts == ["v1", "events"]:
            size = int(self.headers.get("Content-Length", 0))
            if size > 1_000_000:
                return self.send(413, {"error": "event too large"})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            validate_event(body)
            self.store.event(body)
            return self.send(202, {"accepted": True})
        if len(parts) >= 4 and parts[:2] == ["v1", "users"]:
            user = parts[2]
            if self.command == "DELETE" and len(parts) == 6 and parts[3] == "items":
                self.store.forget(user, parts[4], parts[5])
                return self.send(200, {"forgotten": True})
            if self.command == "POST" and len(parts) == 6 and parts[3] == "sources" and parts[5] in ("connect", "disconnect"):
                self.store.connection(user, parts[4], parts[5] == "connect")
                return self.send(200, {"connected": parts[5] == "connect"})
            if self.command == "GET" and len(parts) == 4 and parts[3] == "recall":
                k = max(0, min(100, int(query.get("k", [10])[0])))
                return self.send(200, {"memories": [public(m) for m in self.store.recall(user, query.get("q", [""])[0], k)]})
            if self.command == "GET" and parts[3] == "memories":
                if len(parts) == 5:
                    memories = self.store.memories(user, mid=parts[4])
                    memory = memories[0] if memories else None
                    return self.send(200, public(memory)) if memory else self.send(404, {"error": "not found"})
                if len(parts) == 4:
                    limit = max(1, min(2000, int(query.get("limit", [500])[0])))
                    cursor = query.get("cursor", [""])[0]
                    page = self.store.memories(user, cursor=cursor, limit=limit + 1)
                    return self.send(200, {"memories": [public(m) for m in page[:limit]],
                                           "next_cursor": page[limit - 1]["id"] if len(page) > limit else None})
        self.send(404, {"error": "not found"})

    def handle_request(self):
        try:
            self.route()
        except (ValueError, TypeError) as exc:
            self.send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception("request failed")
            self.send(503, {"error": "temporarily unavailable"})

    do_GET = do_POST = do_DELETE = handle_request


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(os.environ["DATABASE_URL"])
    workers = Workers(store)
    workers.start()
    handler = type("BoundHandler", (Handler,), {"store": store})
    server = Server(("0.0.0.0", int(os.environ.get("PORT", 8080))), handler)
    logging.info("ready on :8080; durable queues and 20 worker slots")
    server.serve_forever()


if __name__ == "__main__":
    main()
