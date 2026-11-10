from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from memkit import MemkitClient


def memory_id(user, kind, key):
    digest = hashlib.sha256(json.dumps([user, kind, key]).encode()).hexdigest()[:32]
    return f"{kind}_{digest}"


class Store:
    def __init__(self, url):
        self.pool = ConnectionPool(url, min_size=2, max_size=40,
                                   kwargs={"row_factory": dict_row}, open=True)
        self.pool.wait(timeout=30)
        with self.pool.connection() as conn:
            conn.execute(Path(__file__).with_name("schema.sql").read_text())
            # One application instance owns the scheduler. Recover all interrupted work.
            conn.execute("UPDATE jobs SET state='pending', ready_at=now() WHERE state='running'")

    @contextmanager
    def write(self, user):
        with self.pool.connection() as conn:
            with conn.transaction():
                # Serialize short mutations for one user, never a model call.
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user,))
                yield conn

    @staticmethod
    def enqueue(conn, user, kind, target, lane, delay=0):
        conn.execute("""INSERT INTO jobs(user_id,kind,target,lane,ready_at)
            VALUES (%s,%s,%s,%s,now() + %s * interval '1 second')
            ON CONFLICT(user_id,kind,target) DO UPDATE SET
            revision=jobs.revision+1, state='pending', attempts=0, error=NULL,
            ready_at=LEAST(jobs.ready_at, EXCLUDED.ready_at)""",
                     (user, kind, target, lane, delay))

    def event(self, event):
        user, source, item = (event[k] for k in ("user_id", "source_id", "item_id"))
        with self.write(user) as conn:
            conn.execute("INSERT INTO sources(user_id,source_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         (user, source))
            current = conn.execute("SELECT * FROM items WHERE user_id=%s AND source_id=%s AND item_id=%s",
                                   (user, source, item)).fetchone()
            if current and (current["forgotten"] or event["version"] <= current["version"]):
                return
            conn.execute("""INSERT INTO items(user_id,source_id,item_id,version,kind,content)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,source_id,item_id)
                DO UPDATE SET version=EXCLUDED.version,kind=EXCLUDED.kind,content=EXCLUDED.content""",
                         (user, source, item, event["version"], event["kind"], event.get("content")))
            self.enqueue(conn, user, "extract", json.dumps([source, item]), source)

    def forget(self, user, source, item):
        with self.write(user) as conn:
            topics = conn.execute("""SELECT DISTINCT f.topic FROM edges e JOIN facts f USING(user_id,fact_key)
                WHERE e.user_id=%s AND e.source_id=%s AND e.item_id=%s""", (user, source, item)).fetchall()
            # Unknown items also receive a durable permanent tombstone.
            conn.execute("""INSERT INTO items(user_id,source_id,item_id,forgotten)
                VALUES (%s,%s,%s,true) ON CONFLICT(user_id,source_id,item_id)
                DO UPDATE SET forgotten=true,content=NULL""", (user, source, item))
            removed = conn.execute("DELETE FROM edges WHERE user_id=%s AND source_id=%s AND item_id=%s RETURNING fact_key",
                                   (user, source, item)).fetchall()
            self.prune(conn, user, [r["fact_key"] for r in removed])
            conn.execute("DELETE FROM jobs WHERE user_id=%s AND kind='extract' AND target=%s",
                         (user, json.dumps([source, item])))
            for row in topics:
                self.enqueue(conn, user, "summary", row["topic"], "~summary")

    def connection(self, user, source, connected):
        with self.write(user) as conn:
            current = conn.execute("SELECT connected FROM sources WHERE user_id=%s AND source_id=%s",
                                   (user, source)).fetchone()
            if current and current["connected"] == connected:
                return
            conn.execute("""INSERT INTO sources(user_id,source_id,connected) VALUES (%s,%s,%s)
                ON CONFLICT(user_id,source_id) DO UPDATE SET connected=EXCLUDED.connected""", (user, source, connected))
            topics = conn.execute("""SELECT DISTINCT f.topic FROM edges e JOIN facts f USING(user_id,fact_key)
                WHERE e.user_id=%s AND e.source_id=%s""", (user, source)).fetchall()
            for row in topics:
                self.enqueue(conn, user, "summary", row["topic"], "~summary")

    @staticmethod
    def live_facts(conn, user, topic=None):
        return conn.execute("""SELECT f.* FROM facts f WHERE f.user_id=%s
            AND (%s::text IS NULL OR f.topic=%s) AND EXISTS (
                SELECT 1 FROM edges e JOIN items i USING(user_id,source_id,item_id)
                JOIN sources s USING(user_id,source_id)
                WHERE e.user_id=f.user_id AND e.fact_key=f.fact_key
                AND i.kind='upsert' AND NOT i.forgotten AND s.connected)
            ORDER BY f.fact_key""",
                            (user, topic, topic)).fetchall()

    @staticmethod
    def prune(conn, user, keys):
        conn.execute("""DELETE FROM facts f WHERE f.user_id=%s AND f.fact_key=ANY(%s::text[])
            AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.user_id=f.user_id AND e.fact_key=f.fact_key)""",
                     (user, keys))

    @staticmethod
    def backed_keys(conn, user, keys):
        # Probe one backing edge per key, rather than scanning every backer of a
        # popular fact during a large backfill.
        rows = conn.execute("""SELECT fact_key FROM unnest(%s::text[]) AS keys(fact_key)
            WHERE EXISTS (SELECT 1 FROM edges e JOIN items i USING(user_id,source_id,item_id)
                JOIN sources s USING(user_id,source_id) WHERE e.user_id=%s AND e.fact_key=keys.fact_key
                AND i.kind='upsert' AND NOT i.forgotten AND s.connected)""", (keys, user)).fetchall()
        return {row["fact_key"] for row in rows}

    def memories(self, user, query=None, k=10, mid=None, cursor="", limit=None):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                rows = self.live_facts(conn, user)
                summaries = conn.execute("SELECT * FROM summaries WHERE user_id=%s", (user,)).fetchall()
                atomics, topics = {}, {}
                for row in rows:
                    key = row["fact_key"]
                    atomics[key] = {"id": memory_id(user, "atomic", key), "kind": "atomic",
                                    "fact_key": key, "topic": row["topic"], "text": row["text"],
                                    "lineage": {"items": [], "memories": []}, "_vector": row["vector"],
                                    "_members": [key]}
                    topics.setdefault(row["topic"], []).append(key)
                result = list(atomics.values())
                for summary in summaries:
                    keys = sorted(topics.get(summary["topic"], []))
                    # Validate the entire member set before exposing any summary text.
                    if len(keys) < 3 or keys != summary["members"]:
                        continue
                    result.append({"id": memory_id(user, "summary", summary["topic"]), "kind": "summary",
                                   "topic": summary["topic"], "text": summary["text"], "_vector": summary["vector"],
                                   "_members": keys, "lineage": {"items": [],
                                   "memories": [atomics[key]["id"] for key in keys]}})
                if query is not None and result:
                    scores = np.asarray([m["_vector"] for m in result]) @ np.asarray(MemkitClient.embed(query))
                    for memory, score in zip(result, scores):
                        memory["score"] = float(score)
                    result.sort(key=lambda m: (-m["score"], m["id"]))
                    result = result[:k]
                else:
                    result.sort(key=lambda m: m["id"])
                    result = [m for m in result if m["id"] > cursor and (mid is None or m["id"] == mid)]
                    if limit is not None:
                        result = result[:limit]
                # Expand only selected lineages, inside the same consistent read snapshot.
                keys = sorted({key for m in result for key in m["_members"]})
                edges = conn.execute("""SELECT e.fact_key,e.source_id,e.item_id FROM edges e
                    JOIN items i USING(user_id,source_id,item_id) JOIN sources s USING(user_id,source_id)
                    WHERE e.user_id=%s AND e.fact_key=ANY(%s::text[]) AND i.kind='upsert'
                    AND NOT i.forgotten AND s.connected""", (user, keys)).fetchall()
                backers = {}
                for edge in edges:
                    backers.setdefault(edge["fact_key"], set()).add((edge["source_id"], edge["item_id"]))
                for memory in result:
                    items = sorted(set().union(*(backers.get(key, set()) for key in memory["_members"])))
                    memory["lineage"]["items"] = [{"source_id": s, "item_id": i} for s, i in items]
                return result

    def recall(self, user, query, k):
        return self.memories(user, query=query, k=k)
