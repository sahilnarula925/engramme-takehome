CREATE TABLE IF NOT EXISTS sources (
    user_id text NOT NULL, source_id text NOT NULL,
    connected boolean NOT NULL DEFAULT true,
    PRIMARY KEY (user_id, source_id)
);
CREATE TABLE IF NOT EXISTS items (
    user_id text NOT NULL, source_id text NOT NULL, item_id text NOT NULL,
    version bigint NOT NULL DEFAULT 0, kind text NOT NULL DEFAULT 'delete',
    content text, forgotten boolean NOT NULL DEFAULT false,
    derived_version bigint,
    PRIMARY KEY (user_id, source_id, item_id)
);
CREATE TABLE IF NOT EXISTS facts (
    user_id text NOT NULL, fact_key text NOT NULL, topic text NOT NULL,
    text text NOT NULL, vector double precision[] NOT NULL,
    PRIMARY KEY (user_id, fact_key)
);
CREATE INDEX IF NOT EXISTS facts_topic ON facts(user_id, topic);
CREATE TABLE IF NOT EXISTS edges (
    user_id text NOT NULL, source_id text NOT NULL, item_id text NOT NULL,
    fact_key text NOT NULL,
    PRIMARY KEY (user_id, source_id, item_id, fact_key),
    FOREIGN KEY (user_id, source_id, item_id) REFERENCES items,
    FOREIGN KEY (user_id, fact_key) REFERENCES facts
);
CREATE INDEX IF NOT EXISTS edges_fact ON edges(user_id, fact_key);
CREATE TABLE IF NOT EXISTS summaries (
    user_id text NOT NULL, topic text NOT NULL, text text NOT NULL,
    vector double precision[] NOT NULL, members text[] NOT NULL,
    PRIMARY KEY (user_id, topic)
);
CREATE TABLE IF NOT EXISTS jobs (
    id bigserial PRIMARY KEY, user_id text NOT NULL, lane text NOT NULL,
    kind text NOT NULL, target text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
    ready_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(), error text,
    UNIQUE(user_id, kind, target)
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, ready_at, id);
CREATE INDEX IF NOT EXISTS jobs_lane_ready ON jobs(user_id,lane,ready_at,id) WHERE state='pending';
