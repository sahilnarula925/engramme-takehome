from __future__ import annotations

import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from memkit import AtomicMemory, MemkitClient

from .store import Store
from .scheduling import FairOrder

log = logging.getLogger(__name__)


class Workers:
    """A single dispatcher owns the shared 20-call budget and fair durable queues."""

    def __init__(self, store: Store, limit=20):
        self.store = store
        self.client = MemkitClient()
        self.limit = limit
        self.stop = threading.Event()
        self.order = FairOrder()
        self.candidates = []
        self.refresh_at = 0
        self.thread = threading.Thread(target=self.run, name="dispatcher", daemon=True)

    def start(self):
        self.thread.start()

    def claim(self, active_ids=()):
        with self.store.pool.connection() as conn:
            # Discover lanes in batches, then use indexed head lookups. Scanning a
            # 20,000-item queue once per dispatched call otherwise bottlenecks the pool.
            now = time.monotonic()
            if now >= self.refresh_at:
                self.candidates = conn.execute("""SELECT DISTINCT ON(user_id,lane) id,user_id,lane FROM jobs
                    WHERE state='pending' AND ready_at <= now()
                    ORDER BY user_id,lane,ready_at,id""").fetchall()
                self.refresh_at = now + 0.1
            def head(lane):
                return conn.execute("""SELECT * FROM jobs WHERE user_id=%s AND lane=%s AND state='pending'
                    AND ready_at <= now() AND NOT(id=ANY(%s::bigint[])) ORDER BY ready_at,id LIMIT 1""",
                                   (lane["user_id"], lane["lane"], list(active_ids))).fetchone()
            job = self.order.next_ready(self.candidates, head)
            if not job:
                return None
            claimed = conn.execute("""UPDATE jobs SET state='running',ready_at=now()
                WHERE id=%s AND revision=%s AND state='pending' RETURNING *""",
                                   (job["id"], job["revision"])).fetchone()
            if claimed:
                self.order.served(job)
            return claimed

    def run(self):
        with ThreadPoolExecutor(self.limit, thread_name_prefix="derive") as pool:
            active = {}
            while not self.stop.is_set():
                active = {jid: f for jid, f in active.items() if not f.done()}
                try:
                    while len(active) < self.limit:
                        job = self.claim(active)
                        if not job:
                            break
                        active[job["id"]] = pool.submit(self.process, job)
                except Exception:
                    log.exception("dispatch failed; retrying")
                self.stop.wait(0.02)

    @staticmethod
    def valid_job(conn, job):
        return conn.execute("SELECT 1 FROM jobs WHERE id=%s AND revision=%s AND state='running'",
                            (job["id"], job["revision"])).fetchone() is not None

    def process(self, job):
        try:
            if job["kind"] == "extract":
                self.extract(job)
            else:
                self.consolidate(job)
            with self.store.pool.connection() as conn:
                conn.execute("DELETE FROM jobs WHERE id=%s AND revision=%s", (job["id"], job["revision"]))
        except Exception as exc:
            delay = min(10, 0.1 * 2 ** min(job["attempts"], 7)) * random.uniform(0.5, 1.5)
            log.warning("job %s (%s) failed: %s; retry in %.2fs", job["id"], job["kind"], exc, delay)
            with self.store.pool.connection() as conn:
                conn.execute("""UPDATE jobs SET state='pending',attempts=attempts+1,error=%s,
                    ready_at=now() + %s * interval '1 second' WHERE id=%s AND revision=%s""",
                             (str(exc)[:500], delay, job["id"], job["revision"]))

    def extract(self, job):
        user = job["user_id"]
        source, item = json.loads(job["target"])
        with self.store.pool.connection() as conn:
            snapshot = conn.execute("SELECT * FROM items WHERE user_id=%s AND source_id=%s AND item_id=%s",
                                    (user, source, item)).fetchone()
        if not snapshot:
            return
        facts = self.client.extract(snapshot["content"]) if snapshot["kind"] == "upsert" and not snapshot["forgotten"] else []
        with self.store.write(user) as conn:
            current = conn.execute("SELECT * FROM items WHERE user_id=%s AND source_id=%s AND item_id=%s",
                                   (user, source, item)).fetchone()
            if not self.valid_job(conn, job) or current["version"] != snapshot["version"] or current["forgotten"]:
                return
            old = conn.execute("""SELECT f.topic,f.fact_key FROM edges e JOIN facts f USING(user_id,fact_key)
                WHERE e.user_id=%s AND e.source_id=%s AND e.item_id=%s""", (user, source, item)).fetchall()
            topics = {r["fact_key"]: r["topic"] for r in old}
            topics.update({f.fact_key: f.topic for f in facts})
            before = self.store.backed_keys(conn, user, list(topics))
            conn.execute("DELETE FROM edges WHERE user_id=%s AND source_id=%s AND item_id=%s", (user, source, item))
            for fact in facts:
                conn.execute("""INSERT INTO facts(user_id,fact_key,topic,text,vector) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (user, fact.fact_key, fact.topic, fact.text, self.client.embed(fact.text)))
                conn.execute("INSERT INTO edges VALUES (%s,%s,%s,%s)", (user, source, item, fact.fact_key))
            self.store.prune(conn, user, [r["fact_key"] for r in old])
            conn.execute("UPDATE items SET derived_version=version WHERE user_id=%s AND source_id=%s AND item_id=%s",
                         (user, source, item))
            after = self.store.backed_keys(conn, user, list(topics))
            dirty = {topics[key] for key in before ^ after}
            # Upstream deletion already changed visibility at acceptance time.
            if snapshot["kind"] == "delete":
                dirty.update(r["topic"] for r in old)
            for topic in dirty:
                self.store.enqueue(conn, user, "summary", topic, "~summary", delay=0.25)

    def consolidate(self, job):
        user, topic = job["user_id"], job["target"]
        with self.store.pool.connection() as conn:
            rows = self.store.live_facts(conn, user, topic)
            existing = conn.execute("SELECT members FROM summaries WHERE user_id=%s AND topic=%s", (user, topic)).fetchone()
        facts = {r["fact_key"]: AtomicMemory(r["fact_key"], r["topic"], r["text"]) for r in rows}
        members = sorted(facts)
        if existing and existing["members"] == members:
            return
        summary = self.client.consolidate([facts[k] for k in members]) if len(members) >= 3 else None
        with self.store.write(user) as conn:
            if not self.valid_job(conn, job):
                return
            current = sorted({r["fact_key"] for r in self.store.live_facts(conn, user, topic)})
            if current != members:
                self.store.enqueue(conn, user, "summary", topic, "~summary", delay=0.25)
                return
            if summary:
                conn.execute("""INSERT INTO summaries(user_id,topic,text,vector,members) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(user_id,topic) DO UPDATE SET text=EXCLUDED.text,vector=EXCLUDED.vector,members=EXCLUDED.members""",
                             (user, topic, summary.text, self.client.embed(summary.text), members))
            else:
                conn.execute("DELETE FROM summaries WHERE user_id=%s AND topic=%s", (user, topic))
