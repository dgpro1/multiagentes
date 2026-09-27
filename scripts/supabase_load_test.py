#!/usr/bin/env python3
"""Load test for phase B: can a client's own Supabase carry a busy WhatsApp line?

Replays the database work of one inbound turn (store the visitor's message,
read the recent history, store the reply) against a Supabase project, at the
rate of a heavy client, and reports latency percentiles. Everything runs in a
throwaway schema that is dropped at the end.

    SUPABASE_TEST_DSN='postgresql://...pooler.supabase.com:6543/postgres' \
        python scripts/supabase_load_test.py --messages-per-day 20000 --peak 10 --seconds 120

Use the transaction pooler string (port 6543) of a TEST project, from the
Supabase dashboard (Connect -> Transaction pooler); never a production one.
The DSN is read from the environment so it stays out of shell history and chat.
Needs psycopg (apps/api/requirements.txt).
"""
import argparse
import os
import random
import statistics
import sys
import threading
import time
import uuid

import psycopg

RUN = f"lt_{uuid.uuid4().hex[:8]}"
# Filled in by setup(): a throwaway schema when the role may create one, otherwise
# prefixed tables in the role's own schema (HunterAI's role may only use "hunterai").
CONV = MSG = ""
DROP: list[str] = []
HISTORY = 30


def connect(dsn: str):
    # The transaction pooler cannot keep prepared statements between transactions.
    return psycopg.connect(dsn, connect_timeout=10, prepare_threshold=None, autocommit=False)


def setup(dsn: str, conversations: int) -> list[uuid.UUID]:
    global CONV, MSG
    ids = [uuid.uuid4() for _ in range(conversations)]
    with connect(dsn) as conn:
        try:
            conn.execute(f"create schema {RUN}")
            CONV, MSG = f"{RUN}.conversations", f"{RUN}.messages"
            DROP.append(f"drop schema {RUN} cascade")
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
            schema = conn.execute("select current_schema()").fetchone()[0]
            CONV, MSG = f"{schema}.{RUN}_conversations", f"{schema}.{RUN}_messages"
            DROP.extend([f"drop table if exists {MSG}", f"drop table if exists {CONV}"])
        conn.execute(f"create table {CONV} (id uuid primary key, updated_at timestamptz not null default now())")
        conn.execute(f"""create table {MSG} (
            id uuid primary key, conversation_id uuid not null references {CONV}(id),
            role text not null, content text not null, llm_content text, tool_calls jsonb,
            created_at timestamptz not null default now())""")
        conn.execute(f"create index on {MSG} (conversation_id, created_at)")
        with conn.cursor() as cur:
            cur.executemany(f"insert into {CONV} (id) values (%s)", [(i,) for i in ids])
        conn.commit()
    return ids


def turn(conn, conversation_id: uuid.UUID) -> float:
    """One inbound message, as the reply pipeline touches the database."""
    body = "Hola, quisiera saber el precio y si hay hora mañana. " * 3
    start = time.perf_counter()
    with conn.transaction():
        conn.execute(f"insert into {MSG} (id, conversation_id, role, content) values (%s, %s, 'user', %s)",
                     (uuid.uuid4(), conversation_id, body))
        conn.execute(f"select role, content from {MSG} where conversation_id = %s order by created_at desc limit {HISTORY}",
                     (conversation_id,)).fetchall()
        conn.execute(f"insert into {MSG} (id, conversation_id, role, content, tool_calls) values (%s, %s, 'assistant', %s, %s)",
                     (uuid.uuid4(), conversation_id, body, '[{"name": "check_calendar_availability"}]'))
        conn.execute(f"update {CONV} set updated_at = now() where id = %s", (conversation_id,))
    return (time.perf_counter() - start) * 1000


def worker(dsn, ids, rate_per_second, deadline, results, errors, lock):
    try:
        conn = connect(dsn)
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(f"connect: {type(exc).__name__}")
        return
    with conn:
        while time.time() < deadline:
            time.sleep(random.expovariate(rate_per_second))
            try:
                elapsed = turn(conn, random.choice(ids))
                with lock:
                    results.append(elapsed)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(type(exc).__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--messages-per-day", type=int, default=20000)
    parser.add_argument("--peak", type=float, default=10.0, help="multiplier over the daily average (peak hour)")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8, help="concurrent connections, like reply tasks in flight")
    parser.add_argument("--conversations", type=int, default=1000)
    args = parser.parse_args()

    dsn = os.environ.get("SUPABASE_TEST_DSN", "").strip()
    if not dsn:
        print("Set SUPABASE_TEST_DSN to a TEST project's transaction pooler string.", file=sys.stderr)
        return 2
    rate = args.messages_per_day / 86400 * args.peak
    print(f"Run {RUN}: {rate:.2f} inbound messages/s over {args.workers} connections for {args.seconds}s")
    ids = setup(dsn, args.conversations)
    results: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    deadline = time.time() + args.seconds
    threads = [threading.Thread(target=worker, args=(dsn, ids, rate / args.workers, deadline, results, errors, lock))
               for _ in range(args.workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        with connect(dsn) as conn:
            for statement in DROP:
                conn.execute(statement)
            conn.commit()

    if not results:
        print(f"No turn completed. Errors: {errors[:10]}")
        return 1
    ordered = sorted(results)
    pct = lambda p: ordered[min(len(ordered) - 1, int(len(ordered) * p))]  # noqa: E731
    print(f"Turns: {len(results)}  errors: {len(errors)} {sorted(set(errors))[:5]}")
    print(f"Latency per turn (ms): p50 {pct(.50):.1f}  p95 {pct(.95):.1f}  p99 {pct(.99):.1f}  "
          f"mean {statistics.mean(results):.1f}  max {ordered[-1]:.1f}")
    verdict = "PASS" if pct(.95) < 150 and not errors else "REVIEW"
    print(f"Decision rule (p95 < 150 ms, no errors): {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
