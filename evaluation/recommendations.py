"""Compare the original genre ranker with completion weighting and popularity.

Run: python -m evaluation.recommendations --database-url URL
Reads an existing database without changing users, history, likes, or saved lists.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from math import log2
from pathlib import Path
from statistics import mean

import psycopg

from db import get_connection


ROOT = Path(__file__).resolve().parents[1]
QUERY_FILES = {
    "popularity": "popularity_candidates.sql",
    "genre_before": "genre_baseline.sql",
    "genre_after": "genre_candidates.sql",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def ranking_metrics(tracks, relevant, k=10):
    if k < 1 or len(tracks) > k or len(set(tracks)) != len(tracks):
        raise ValueError("Expected at most k unique recommendations and k > 0")
    relevant = set(relevant)
    hits = len(set(tracks) & relevant)
    ideal = sum(1 / log2(rank + 2) for rank in range(min(k, len(relevant))))
    gain = sum(1 / log2(rank + 2) for rank, track in enumerate(tracks) if track in relevant)
    return {"hits": hits, "precision": hits / k,
            "recall": hits / len(relevant) if relevant else None,
            "ndcg": gain / ideal if ideal else None}


def listening_targets(users, durations, events, cutoff, end):
    """Any prior event excludes a song; future targets need >=80% completion."""
    seen = {uid: set() for uid in users}
    future = {uid: set() for uid in users}
    for uid, track, played, listened in events:
        if played < cutoff:
            seen[uid].add(track)
        elif played < end and listened * 5 >= durations[track] * 4:
            future[uid].add(track)
    return seen, {uid: future[uid] - seen[uid] for uid in users}


def summarize(per_user, methods, catalog_tracks, k):
    """Quality uses eligible users; coverage and list completeness use all users."""
    summary = {}
    for group, members in {
        "all": per_user,
        "warm": [row for row in per_user if not row["cold_start"]],
        "cold_start": [row for row in per_user if row["cold_start"]],
    }.items():
        eligible = [row for row in members if row["relevant_tracks"]]
        summary[group] = {}
        for method in methods:
            metrics = [row["methods"][method] for row in eligible]
            lists = [row["methods"][method]["tracks"] for row in members]
            distinct = len({track for tracks in lists for track in tracks})
            summary[group][method] = {
                "users": len(members), "eligible_users": len(eligible),
                "excluded_users": len(members) - len(eligible),
                "hits": sum(row["hits"] for row in metrics),
                **{name: mean(row[name] for row in metrics) if metrics else None
                   for name in ("precision", "recall", "ndcg")},
                "distinct_recommended_tracks": distinct,
                "catalog_coverage": distinct / catalog_tracks if members and catalog_tracks else None,
                "full_list_rate": mean(len(tracks) == k for tracks in lists) if lists else None,
                "mean_returned": mean(len(tracks) for tracks in lists) if lists else None,
            }
    return summary


def evaluate(conn, cutoff, end, k=10):
    if cutoff >= end or k < 1:
        raise ValueError("Expected cutoff < end and k > 0")
    users = [uid for uid, name in conn.execute("SELECT user_id,username FROM public.users ORDER BY user_id")
             if name.startswith("synthetic_v1_")]
    if not users:
        raise ValueError("No synthetic_v1 listeners found in the existing database")
    catalog = conn.execute("""SELECT s.track_id,s.duration_ms,s.track_popularity,
        array_agg(sg.genre_id ORDER BY sg.genre_id)
        FROM public.songs s LEFT JOIN public.song_genres sg USING(track_id)
        GROUP BY s.track_id ORDER BY s.track_id""").fetchall()
    durations = {row[0]: row[1] for row in catalog}
    events = conn.execute("""SELECT user_id,track_id,played_when,listened_ms
        FROM public.history WHERE user_id=ANY(%s) AND played_when < %s
        ORDER BY user_id,played_when,track_id,listened_ms""", (users, end)).fetchall()
    seen, targets = listening_targets(users, durations, events, cutoff, end)
    queries = {name: (ROOT / "queries" / filename).read_text() for name, filename in QUERY_FILES.items()}
    per_user = []
    for uid in users:
        relevant = targets[uid]
        row = {"user_id": uid, "cold_start": not seen[uid],
               "relevant_tracks": sorted(relevant), "methods": {}}
        for method, query in queries.items():
            tracks = [result[0] for result in conn.execute(query, {"user_id": uid, "cutoff": cutoff, "k": k})]
            if seen[uid].intersection(tracks):
                raise AssertionError(f"Previously heard track returned by {method}")
            row["methods"][method] = {"tracks": tracks, **ranking_metrics(tracks, relevant, k)}
        if row["cold_start"] and row["methods"]["genre_after"]["tracks"] != row["methods"]["popularity"]["tracks"]:
            raise AssertionError("Cold-start fallback changed")
        per_user.append(row)
    return {"cutoff": cutoff.isoformat(), "end_exclusive": end.isoformat(), "k": k,
            "catalog_tracks": len(catalog),
            "input_sha256": fingerprint({"users": users, "catalog_history_sha256": fingerprint([catalog, events])}),
            "query_sha256": {name: fingerprint(query) for name, query in queries.items()},
            "summary": summarize(per_user, queries, len(catalog), k), "per_user": per_user}


def markdown_report(report):
    lines = ["# Recommendation quality: before and after", "",
             "The new genre ranker gives a full play one unit of evidence, regardless of track length. "
             "Partial plays contribute their completion fraction. Evidence is divided across genre labels; "
             "candidate averaging, popularity tie-breaking, unheard-song exclusion, and cold-start behavior stay the same. "
             "The current query fills short genre lists with remaining unheard songs ordered by popularity.", "",
             "This script compares three fixed queries: popularity, the original duration-weighted genre baseline, "
             "and the current completion-weighted genre query with popularity top-up. July–August is the validation "
             "window and September–October is the test window. The current comparison includes the top-up change; "
             "it does not isolate completion weighting or reproduce earlier model-selection experiments.", "",
             "A relevant item is a distinct future song, unheard before the cutoff, with at least one "
             "play reaching 80% completion. Precision divides by K even if fewer songs are returned; "
             "recall and binary NDCG use those relevant songs. Metrics are macro averages over eligible users. "
             "Users without relevant targets are excluded from quality metrics and counted separately.", "",
             "Coverage and list completeness include every requested user's list, including users without future targets. "
             "Catalog coverage is the number of distinct recommended songs divided by the entire catalog size. "
             "Full-list rate is the fraction of requests returning exactly K songs; mean returned is the mean list length. "
             "A small remaining unheard catalog can legitimately lower full-list rate. Empty cohorts have N/A rates; "
             "coverage is also N/A for an empty catalog.", ""]
    for phase in ("validation", "test"):
        result = report[phase]
        k = result["k"]
        lines += [f"## {phase.title()}", "", f"History before {result['cutoff']}; future activity before {result['end_exclusive']}.", "",
                  f"| Group | Method | Eligible / users | Hits@{k} | Precision@{k} | Recall@{k} | NDCG@{k} |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for group, methods in result["summary"].items():
            for method, values in methods.items():
                percentages = ["N/A" if values[key] is None else f"{values[key]:.4%}" for key in ("precision", "recall", "ndcg")]
                lines.append(f"| {group} | {method} | {values['eligible_users']} / {values['users']} | {values['hits']} | " + " | ".join(percentages) + " |")
        lines += ["", f"Catalog denominator: {result['catalog_tracks']:,} songs. Quality excludes users without relevant targets.", "",
                  "| Group | Method | Requests | Distinct songs | Catalog coverage | Full-list rate | Mean returned |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for group, methods in result["summary"].items():
            for method, values in methods.items():
                coverage = "N/A" if values["catalog_coverage"] is None else f"{values['catalog_coverage']:.4%}"
                full = "N/A" if values["full_list_rate"] is None else f"{values['full_list_rate']:.2%}"
                returned = "N/A" if values["mean_returned"] is None else f"{values['mean_returned']:.2f}"
                lines.append(f"| {group} | {method} | {values['users']} | {values['distinct_recommended_tracks']} | {coverage} | {full} | {returned} |")
        lines.append("")
    lines += ["## Limits", "",
              "- This is a small synthetic sample with few exact-track hits; differences are not evidence of statistical significance or real-user satisfaction.",
              "- The September–October period has already been inspected during development; this rerun is a regression comparison, not a newly untouched test set.",
              "- The generator selects favorite identities randomly and repeats them often. Already-heard favorites are intentionally excluded from discovery targets.",
              "- Catalog metadata is a fixed historical snapshot; no historical popularity snapshots are available.",
              "- Likes are not used. Synthetic initial likes were selected using the whole generation period, so their timestamps alone cannot prevent evaluation leakage.",
              "- The JSON stores input/query fingerprints and per-user targets/rankings. No database URL or credentials are saved.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Existing database; defaults to project DATABASE_URL/.env; always read-only")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/recommendation_quality.json")
    args = parser.parse_args()
    connection = psycopg.connect(args.database_url, connect_timeout=15) if args.database_url else get_connection(read_only=True)
    with connection as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.execute("SET LOCAL statement_timeout = '60s'")
        report = {"model": "completion_weighted_genre_average_with_popularity_top_up", "server_version": conn.info.server_version}
        for phase, cutoff, end in [("validation", "2026-07-01", "2026-09-01"), ("test", "2026-09-01", "2026-11-01")]:
            report[phase] = evaluate(conn, datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc),
                                     datetime.fromisoformat(end).replace(tzinfo=timezone.utc), args.k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.with_suffix(".md").write_text(markdown_report(report))
    print(f"Wrote {args.output} and its Markdown summary (read-only evaluation).")


if __name__ == "__main__":
    main()
