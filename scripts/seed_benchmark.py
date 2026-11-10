"""Bulk-load a declared read-load fixture, not an ingestion correctness test.

Run inside the app container, which has psycopg and the supplied reference models.
This script never constructs Store (which would reset the application's job claims).
"""
import json
import os
import string
import uuid

import psycopg
from memkit import core

prefix = "bench-" + uuid.uuid4().hex[:8]
topics = []
for n in range(40):
    project = "Topic" + string.ascii_lowercase[n // 26] + string.ascii_lowercase[n % 26]
    content = (f"Ana is leading the {project} project. Ben joined the {project} project. "
               f"The {project} project budget is 120000 dollars. The {project} project deadline is March 3.")
    facts = core.extract(content)
    summary = core.consolidate(facts)
    topics.append((content, facts, summary))

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor().copy("COPY sources(user_id,source_id) FROM STDIN") as copy:
        for u in range(500):
            copy.write_row((f"{prefix}-{u}", "archive"))
    with conn.cursor().copy("COPY items(user_id,source_id,item_id,version,kind,content,derived_version) FROM STDIN") as copy:
        for u in range(500):
            for i in range(400):
                copy.write_row((f"{prefix}-{u}", "archive", f"item-{i}", 1, "upsert", topics[i % 40][0], 1))
    with conn.cursor().copy("COPY facts(user_id,fact_key,topic,text,vector) FROM STDIN") as copy:
        for u in range(500):
            for _, facts, _ in topics:
                for f in facts:
                    copy.write_row((f"{prefix}-{u}", f.fact_key, f.topic, f.text, core.embed(f.text)))
    with conn.cursor().copy("COPY edges(user_id,source_id,item_id,fact_key) FROM STDIN") as copy:
        for u in range(500):
            for i in range(400):
                for f in topics[i % 40][1]:
                    copy.write_row((f"{prefix}-{u}", "archive", f"item-{i}", f.fact_key))
    with conn.cursor().copy("COPY summaries(user_id,topic,text,vector,members) FROM STDIN") as copy:
        for u in range(500):
            for _, facts, summary in topics:
                copy.write_row((f"{prefix}-{u}", summary.topic, summary.text, core.embed(summary.text),
                                sorted(f.fact_key for f in facts)))
    conn.execute("ANALYZE")
print(json.dumps({"prefix": prefix, "users": 500, "items": 200000, "atomic_memories": 80000,
                  "summary_memories": 20000, "lineage_edges": 800000}))
