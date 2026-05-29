import os
from datetime import datetime, timezone

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "")

ALGORITHMS = ("taxis", "kinesis")
EARS = ("aligned", "separated")
EVENT_TYPES = ("hit_wall", "hit_robot", "stuck", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              SERIAL PRIMARY KEY,
    robot_name      TEXT        NOT NULL,
    algorithm       TEXT        NOT NULL,
    ears            TEXT        NOT NULL,
    duration_seconds INTEGER    NOT NULL,
    elapsed_seconds REAL        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT runs_algorithm_check CHECK (algorithm IN ('taxis', 'kinesis')),
    CONSTRAINT runs_ears_check      CHECK (ears IN ('aligned', 'separated'))
);

-- Backfill for tables created before elapsed_seconds existed. Old rows ran
-- the full planned duration (there was no End early path on submit), so
-- duration_seconds is a fine default.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS elapsed_seconds REAL;
UPDATE runs SET elapsed_seconds = duration_seconds WHERE elapsed_seconds IS NULL;
ALTER TABLE runs ALTER COLUMN elapsed_seconds SET NOT NULL;

-- Rename ears value 'angled' -> 'separated'. Drop any auto-named CHECK
-- that still references 'angled' before re-adding the canonical one.
DO $$
DECLARE c name;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'runs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%angled%'
    LOOP
        EXECUTE format('ALTER TABLE runs DROP CONSTRAINT %I', c);
    END LOOP;
END $$;

UPDATE runs SET ears = 'separated' WHERE ears = 'angled';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'runs'::regclass AND conname = 'runs_ears_check'
    ) THEN
        ALTER TABLE runs ADD CONSTRAINT runs_ears_check
            CHECK (ears IN ('aligned', 'separated'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS events (
    id         SERIAL PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT    NOT NULL,
    t_seconds  REAL    NOT NULL,
    CHECK (event_type IN ('hit_wall', 'hit_robot', 'stuck', 'other'))
);

CREATE INDEX IF NOT EXISTS events_run_id_idx ON events (run_id);
"""


def _make_pool() -> ConnectionPool:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. On Railway, attach a Postgres plugin; "
            "locally, copy .env.example to .env and fill it in."
        )
    return ConnectionPool(DATABASE_URL, min_size=1, max_size=4, open=True)


def init_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)


def insert_run(
    pool: ConnectionPool,
    *,
    robot_name: str,
    algorithm: str,
    ears: str,
    duration_seconds: int,
    elapsed_seconds: float,
    started_at: datetime,
    events: list[tuple[str, float]],
) -> int:
    """Insert a run and its events in a single transaction. Returns run id."""
    if algorithm not in ALGORITHMS:
        raise ValueError(f"algorithm must be one of {ALGORITHMS}")
    if ears not in EARS:
        raise ValueError(f"ears must be one of {EARS}")
    for ev_type, _ in events:
        if ev_type not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {EVENT_TYPES}")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runs (
                robot_name, algorithm, ears,
                duration_seconds, elapsed_seconds, started_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                robot_name, algorithm, ears,
                duration_seconds, elapsed_seconds, started_at,
            ),
        )
        run_id = cur.fetchone()[0]
        if events:
            cur.executemany(
                "INSERT INTO events (run_id, event_type, t_seconds) VALUES (%s, %s, %s)",
                [(run_id, ev_type, t) for ev_type, t in events],
            )
        return run_id


def fetch_runs_with_counts(pool: ConnectionPool) -> list[dict]:
    """Return all runs with per-event-type counts, newest first."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.id,
                r.robot_name,
                r.algorithm,
                r.ears,
                r.duration_seconds,
                r.elapsed_seconds,
                r.started_at,
                r.submitted_at,
                COALESCE(SUM((e.event_type = 'hit_wall')::int),  0) AS hit_wall,
                COALESCE(SUM((e.event_type = 'hit_robot')::int), 0) AS hit_robot,
                COALESCE(SUM((e.event_type = 'stuck')::int),     0) AS stuck,
                COALESCE(SUM((e.event_type = 'other')::int),     0) AS other,
                COUNT(e.id) AS total_events
            FROM runs r
            LEFT JOIN events e ON e.run_id = r.id
            GROUP BY r.id
            ORDER BY r.started_at DESC
            """
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
