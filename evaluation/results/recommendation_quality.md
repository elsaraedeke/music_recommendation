# Recommendation quality: before and after

The new genre ranker gives a full play one unit of evidence, regardless of track length. Partial plays contribute their completion fraction. Evidence is divided across genre labels; candidate averaging, popularity tie-breaking, unheard-song exclusion, and cold-start behavior stay the same.

The scoring rule was selected using July–August validation, then frozen before measuring the September–October comparison. Ten fixed validation candidates (including baselines) were compared; the selected rule gained only one validation hit. Density and preferred-genre-set alternatives performed worse. The generator, data, K, and target definition were unchanged.

A relevant item is a distinct future song, unheard before the cutoff, with at least one play reaching 80% completion. Precision divides by K even if fewer songs are returned; recall and binary NDCG use those relevant songs. Metrics are macro averages over eligible users. Users without relevant targets are excluded and counted separately.

## Validation

History before 2026-07-01T00:00:00+00:00; future activity before 2026-09-01T00:00:00+00:00.

| Group | Method | Eligible / users | Hits@10 | Precision@10 | Recall@10 | NDCG@10 |
|---|---|---:|---:|---:|---:|---:|
| all | popularity | 92 / 100 | 2 | 0.2174% | 0.0516% | 0.2362% |
| all | genre_before | 92 / 100 | 6 | 0.6522% | 0.1295% | 0.7126% |
| all | genre_after | 92 / 100 | 7 | 0.7609% | 0.1553% | 0.8051% |
| warm | popularity | 92 / 93 | 2 | 0.2174% | 0.0516% | 0.2362% |
| warm | genre_before | 92 / 93 | 6 | 0.6522% | 0.1295% | 0.7126% |
| warm | genre_after | 92 / 93 | 7 | 0.7609% | 0.1553% | 0.8051% |
| cold_start | popularity | 0 / 7 | 0 | N/A | N/A | N/A |
| cold_start | genre_before | 0 / 7 | 0 | N/A | N/A | N/A |
| cold_start | genre_after | 0 / 7 | 0 | N/A | N/A | N/A |

## Test

History before 2026-09-01T00:00:00+00:00; future activity before 2026-11-01T00:00:00+00:00.

| Group | Method | Eligible / users | Hits@10 | Precision@10 | Recall@10 | NDCG@10 |
|---|---|---:|---:|---:|---:|---:|
| all | popularity | 93 / 100 | 2 | 0.2151% | 0.0765% | 0.2203% |
| all | genre_before | 93 / 100 | 2 | 0.2151% | 0.0645% | 0.2203% |
| all | genre_after | 93 / 100 | 2 | 0.2151% | 0.0645% | 0.2203% |
| warm | popularity | 92 / 94 | 2 | 0.2174% | 0.0773% | 0.2226% |
| warm | genre_before | 92 / 94 | 2 | 0.2174% | 0.0652% | 0.2226% |
| warm | genre_after | 92 / 94 | 2 | 0.2174% | 0.0652% | 0.2226% |
| cold_start | popularity | 1 / 6 | 0 | 0.0000% | 0.0000% | 0.0000% |
| cold_start | genre_before | 1 / 6 | 0 | 0.0000% | 0.0000% | 0.0000% |
| cold_start | genre_after | 1 / 6 | 0 | 0.0000% | 0.0000% | 0.0000% |

## Limits

- This is a small synthetic sample with few exact-track hits; differences are not evidence of statistical significance or real-user satisfaction.
- The generator selects favorite identities randomly and repeats them often. Already-heard favorites are intentionally excluded from discovery targets.
- Catalog metadata is a fixed historical snapshot; no historical popularity snapshots are available.
- Likes are not used. Synthetic initial likes were selected using the whole generation period, so their timestamps alone cannot prevent evaluation leakage.
- The JSON stores input/query fingerprints and per-user targets/rankings. No database URL or credentials are saved.
