"""Small history-index experiment on an explicit disposable localhost database.

Candidate indexes are rolled back. Run with --help for the required opt-in.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
INDEXES = {
    "user_date": ("history_user_played_idx", "CREATE INDEX history_user_played_idx ON public.history(user_id, played_when DESC, history_id DESC)"),
    "user_track_date": ("history_user_track_played_idx", "CREATE INDEX history_user_track_played_idx ON public.history(user_id,track_id,played_when)"),
}
PREFIX = """WITH listens AS (
SELECT * FROM public.history WHERE user_id=%(user_id)s
AND played_when >= %(start)s AND played_when < %(end)s
) """
QUERIES = {
    "dashboard_totals": PREFIX + """SELECT COALESCE(SUM(h.listened_ms),0)/60000.0,COUNT(*),
    COUNT(DISTINCT h.track_id),COUNT(DISTINCT s.artist_id)
    FROM listens h JOIN public.songs s ON s.track_id=h.track_id""",
    "listening_history": PREFIX + """SELECT h.history_id,s.track_id,h.played_when AT TIME ZONE 'UTC',
    s.track_name,a.artist_name,h.listened_ms/60000.0,ROUND(100.0*h.listened_ms/s.duration_ms,1)
    FROM listens h JOIN public.songs s ON s.track_id=h.track_id
    JOIN public.artists a ON a.artist_id=s.artist_id
    ORDER BY h.played_when DESC,h.history_id DESC""",
    "genre_recommendations": (ROOT / "queries/genre_candidates.sql").read_text(),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def validate_local_url(url):
    details = conninfo_to_dict(url)
    if details.get("host") not in {"localhost", "127.0.0.1", "::1"} or not details.get("dbname"):
        raise ValueError("Use a direct localhost URL naming a disposable database.")
    if any(key in details for key in ("service", "hostaddr")) or any(os.environ.get(key) for key in ("PGSERVICE", "PGHOSTADDR")):
        raise ValueError("Do not use service/hostaddr overrides for this local experiment.")


def measure(conn, query, params, warmups, repeats):
    for _ in range(warmups):
        conn.execute(query, params, prepare=False).fetchall()
    samples, expected = [], None
    for _ in range(repeats):
        start = perf_counter()
        rows = conn.execute(query, params, prepare=False).fetchall()
        samples.append(1000 * (perf_counter() - start))
        actual = digest(rows)
        if expected is not None and actual != expected:
            raise AssertionError("Results changed during repeated measurements.")
        expected = actual
    plan = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query, params, prepare=False).fetchone()[0][0]
    quartiles = quantiles(samples, n=4)
    return {"median_ms": median(samples), "q1_ms": quartiles[0], "q3_ms": quartiles[2],
            "samples_ms": samples, "result_rows": len(rows), "result_sha256": expected, "plan": plan}


def benchmark(conn, warmups=3, repeats=15):
    address = conn.execute("SELECT inet_server_addr()::text").fetchone()[0]
    if address is None or not ipaddress.ip_interface(address).ip.is_loopback:
        raise ValueError("Connected server is not a localhost TCP server.")
    conn.execute("SET LOCAL statement_timeout='60s'")
    existing = conn.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname").fetchall()
    names = {name for name, _ in INDEXES.values()}
    if names & {row[1] for row in existing}:
        raise ValueError("Candidate indexes already exist; use a fresh disposable rebuild.")
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
              for table in ["users", "songs", "history", "song_genres", "track_likes"]}
    user = conn.execute("SELECT user_id FROM public.history GROUP BY user_id ORDER BY COUNT(*) DESC,user_id LIMIT 1").fetchone()
    if user is None:
        raise ValueError("Load the project's synthetic data before benchmarking.")
    params = {"user_id": user[0], "start": datetime(2026, 5, 1, tzinfo=timezone.utc),
              "end": datetime(2026, 11, 1, tzinfo=timezone.utc), "cutoff": datetime(2026, 9, 1, tzinfo=timezone.utc), "k": 10}
    sizes = conn.execute("SELECT pg_relation_size('public.history'),pg_indexes_size('public.history')").fetchone()
    settings = {key: conn.execute("SHOW " + key).fetchone()[0] for key in
                ["work_mem", "shared_buffers", "effective_cache_size", "random_page_cost", "seq_page_cost", "jit"]}
    for table in ["history", "songs", "artists", "song_genres", "users"]:
        conn.execute("ANALYZE public." + table)
    report = {"environment": {"postgres": conn.info.server_version, "python": platform.python_version(),
                              "system": platform.system(), "architecture": platform.machine(), "settings": settings},
              "counts": counts, "history_heap_bytes": sizes[0], "baseline_history_index_bytes": sizes[1],
              "baseline_indexes": existing, "parameters": params, "warmups": warmups, "repeats": repeats,
              "timing": "execute+fetchall client wall time; warm-cache local direct SQL; automatic prepare disabled",
              "query_sql": QUERIES, "query_sha256": {name: digest(sql) for name, sql in QUERIES.items()},
              "candidate_sql": {key: sql for key, (_, sql) in INDEXES.items()}, "phases": {}}
    for phase, keys in [("baseline", []), ("user_date", ["user_date"]), ("user_track_date", ["user_track_date"]), ("both", list(INDEXES))]:
        conn.execute("SAVEPOINT measurement_phase")
        for key in keys:
            conn.execute(INDEXES[key][1])
        index_sizes = {INDEXES[key][0]: conn.execute("SELECT pg_relation_size(%s::regclass)",
                       ("public." + INDEXES[key][0],)).fetchone()[0] for key in keys}
        queries = {name: measure(conn, query, params, warmups, repeats) for name, query in QUERIES.items()}
        if phase != "baseline":
            for name in QUERIES:
                if queries[name]["result_sha256"] != report["phases"]["baseline"]["queries"][name]["result_sha256"]:
                    raise AssertionError("An index changed query results.")
        report["phases"][phase] = {"index_bytes": index_sizes, "queries": queries}
        conn.execute("ROLLBACK TO SAVEPOINT measurement_phase")
        conn.execute("RELEASE SAVEPOINT measurement_phase")
    conn.rollback()
    actual = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'").fetchall()
    if names & {row[0] for row in actual}:
        raise AssertionError("Candidate indexes were not rolled back.")
    report["candidate_indexes_rolled_back"] = True
    return report


def markdown_report(report):
    lines = ["# Starter history-index experiment", "",
             f"PostgreSQL {report['environment']['postgres'] // 10000}.{report['environment']['postgres'] % 10000}; "
             f"{report['counts']['history']:,} listening events; one active listener ({report['parameters']['user_id']}). "
             f"Each query used {report['warmups']} warmups and {report['repeats']} timed executions per configuration. "
             "Times include execute and fetchall on a local warm-cache connection, excluding Streamlit caching.", "",
             "| Configuration | Dashboard ms | History ms | Genre recommendations ms | Added index bytes |",
             "|---|---:|---:|---:|---:|"]
    for phase, values in report["phases"].items():
        medians = [values["queries"][name]["median_ms"] for name in QUERIES]
        lines.append(f"| {phase} | {medians[0]:.3f} | {medians[1]:.3f} | {medians[2]:.3f} | {sum(values['index_bytes'].values()):,} |")
    def history_nodes(node):
        found = []
        if node.get("Relation Name") == "history" or node.get("Index Name", "").startswith("history_user"):
            found.append(node)
        for child in node.get("Plans", []):
            found.extend(history_nodes(child))
        return found
    lines += ["", "History-access excerpts from the measured plans:", "", "```text"]
    for phase in ("baseline", "user_date"):
        nodes = history_nodes(report["phases"][phase]["queries"]["listening_history"]["plan"]["Plan"])
        lines.append(phase + ":")
        for node in nodes:
            lines.append(f"  {node['Node Type']} {node.get('Index Name', '')}: "
                         f"rows={node['Actual Rows']}, filtered={node.get('Rows Removed by Filter', 0)}")
    lines += ["```", "", f"History heap: {report['history_heap_bytes']:,} bytes. "
              "All result hashes matched, and all candidate indexes were rolled back. No index has been deployed by this experiment. "
              "Compare the individual-index configurations before choosing both; small timing differences are not proof of a benefit. "
              "An index does not guarantee sort elimination; inspect the complete plans.", "",
              "The JSON includes complete EXPLAIN (ANALYZE, BUFFERS) plans, samples, SQL, parameters, settings, and index sizes. "
              "Only one listener/date window and a fixed phase order were measured. No concurrency, remote latency, insert cost, "
              "or significance test was evaluated. Teammates can extend these experiments before choosing deployment indexes.", "",
              "Reproduce on a fresh local rebuild:", "", "```bash",
              "python -m evaluation.benchmark --database-url postgresql://localhost/music_benchmark --allow-index-changes", "```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--allow-index-changes", action="store_true")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/physical_design.json")
    args = parser.parse_args()
    if not args.allow_index_changes or args.warmups < 1 or args.repeats < 2:
        parser.error("Require --allow-index-changes, at least one warmup, and at least two repeats.")
    try:
        validate_local_url(args.database_url)
    except ValueError as exc:
        parser.error(str(exc))
    with psycopg.connect(args.database_url, connect_timeout=15) as conn:
        conn.prepare_threshold = None
        try:
            report = benchmark(conn, args.warmups, args.repeats)
        finally:
            conn.rollback()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    args.output.with_suffix(".md").write_text(markdown_report(report))
    print(f"Saved {args.output} and Markdown summary; candidate indexes rolled back.")


if __name__ == "__main__":
    main()
