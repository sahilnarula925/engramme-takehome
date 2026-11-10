Video: pending recording and upload. Add the unlisted link here before submission.

# Engramme Take-Home — Submission

A memory service for Part 1 and a reproducible statistics analysis for Part 2. The original instructions are preserved unchanged in [ASSIGNMENT.md](ASSIGNMENT.md). Run all commands below from the repository root.

## Run

Requires Docker Desktop and Python 3.10+ for the host-side demo and harness. No host packages or secrets are required.

```sh
docker compose up --build -d --wait
./harness run --scenario basic --seed 73
python3 scripts/demo.py
```

Service: `http://localhost:8080`. Supplied sidecar: `http://localhost:7070`. PostgreSQL is internal to Compose and uses a named volume. A successful write means the item state and its pending work have committed to PostgreSQL. Derivation is asynchronous.

## Statistics (Part 2)

[STATISTICS.md](STATISTICS.md) contains the derivations, numerical results, and tradeoffs for all three levels. Reproduce and verify the results without Docker or third-party packages:

```sh
python3 scripts/statistics.py --verify
python3 -m unittest discover -s tests -p test_statistics.py -v
```

## Test

Run these sequentially; the fault tests and harness both configure the supplied sidecar.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
./harness run --scenario all --seed 73
```

The harness wrapper delegates to the unmodified starter kit. Every run uses fresh users. For a paced walkthrough of remembering, edits, forgetting, and reconnects, run `python3 scripts/demo.py --pause`.

## Structure

- `service/schema.sql`: source state, item versions, lineage, memories, and durable jobs.
- `service/store.py`: short transactional mutations, consistent read snapshots, exact cosine recall, and lineage expansion.
- `service/worker.py`: fair dispatch, 20 shared model slots, retries, coalescing, and stale-result checks.
- `service/scheduling.py`: independently tested fairness policy across users and source lanes.
- `service/__main__.py`: JSON HTTP API and process startup.
- `tests/`: API, failure, scheduling, and statistics checks.
- `scripts/`: narrated demo and repeatable performance fixture/load tools.
- `STATISTICS.md`: Part 2 answers, supported by `scripts/statistics.py`.
- `starter-kit/`: unchanged supplied sidecar, clients, and reference harness.

## Performance check

The seed script bulk-loads a declared search fixture directly into PostgreSQL: 200,000 items across 500 users, 80,000 atomic memories, 20,000 summaries, and 800,000 lineage edges. It uses supplied reference functions for fixture generation only. This measures read scale; it does not claim those items passed through HTTP extraction. The load driver then sends new items through the real API while recalling against that fixture, using 32 total client threads.

```sh
docker compose exec -T app python - < scripts/seed_benchmark.py
# Copy the printed prefix into the next command:
python3 scripts/benchmark.py bench-PRINTEDPREFIX
python3 scripts/stress_backfill.py
```

Each seed run adds another isolated dataset. The load driver exits after measuring acknowledgement and recall, so extraction may still be queued. Before running fault tests, the harness, or a recording, check that this query returns no rows:

```sh
docker compose exec -T db psql -U postgres -c "SELECT kind,state,count(*) FROM jobs GROUP BY kind,state;"
```

Ordinary stop/start retains all data. `docker compose logs -f --tail=20 app` shows retries and startup; `docker compose ps` shows readiness. Run load, harness, and fault tests separately.
