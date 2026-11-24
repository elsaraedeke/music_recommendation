# Starter history-index experiment

Preserved November 22, 2026 measurements at commit `63da6c2`. These precede genre v2's popularity top-up; rerun the benchmark before using these timings for the current recommendation query.

PostgreSQL 16.14; 17,509 listening events; one active listener (341173). Each query used 3 warmups and 15 timed executions per configuration. Times include execute and fetchall on a local warm-cache connection, excluding Streamlit caching.

| Configuration | Dashboard ms | History ms | Genre recommendations ms | Added index bytes |
|---|---:|---:|---:|---:|
| baseline | 3.335 | 5.109 | 38.435 | 0 |
| user_date | 2.485 | 4.147 | 36.310 | 729,088 |
| user_track_date | 2.508 | 4.144 | 36.566 | 1,064,960 |
| both | 2.490 | 4.254 | 36.356 | 1,794,048 |

History-access excerpts from the measured plans:

```text
baseline:
  Seq Scan : rows=297, filtered=17212
user_date:
  Bitmap Heap Scan : rows=297, filtered=0
  Bitmap Index Scan history_user_played_idx: rows=297, filtered=0
```

History heap: 1,572,864 bytes. All result hashes matched, and all candidate indexes were rolled back. No index has been deployed by this experiment. Compare the individual-index configurations before choosing both; small timing differences are not proof of a benefit. An index does not guarantee sort elimination; inspect the complete plans.

The JSON includes complete EXPLAIN (ANALYZE, BUFFERS) plans, samples, SQL, parameters, settings, and index sizes. Only one listener/date window and a fixed phase order were measured. No concurrency, remote latency, insert cost, or significance test was evaluated. Teammates can extend these experiments before choosing deployment indexes.

Reproduce on a fresh local rebuild:

```bash
python -m evaluation.benchmark --database-url postgresql://localhost/music_benchmark --allow-index-changes
```

In this run, the user/date index alone cut dashboard and history medians by about 25.5% and 18.8%. It used 729,088 bytes. Adding both indexes provided no additional median benefit and used 1,794,048 bytes, exceeding the 1,572,864-byte history heap. The history Sort remained; no deployment decision is implied.
