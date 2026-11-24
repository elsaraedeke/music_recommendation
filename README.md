# Music Recommendations and Listening History

## Build a fresh database

Requirements: Python 3.10+ and PostgreSQL 16+ (a running local server or a separate
Neon development database). Run commands from the repository root.

Create a Python environment and install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Choose the target database. For a running local PostgreSQL server:

```bash
createdb music_recommendation
```

Then set this value in `.env`:

```text
DATABASE_URL="postgresql://localhost/music_recommendation"
```

For Neon, put the connection URL for an empty development database in `.env`
instead. Keep credentials out of Git. An exported `DATABASE_URL` takes precedence
over `.env`; `setup_database.py --database-url URL` selects its target explicitly.

Prepare the catalog, initialize the schema, import it, and generate activity:

```bash
python cleaning_scripts/cleaned_spotify.py
python cleaning_scripts/normalize_spotify.py
python setup_database.py
python check_connection.py
```

Setup applies the portable schema, imports five catalog CSVs, and seeds synthetic
users/history/likes in one transaction. A failed import or seed rolls back that
database setup. The cleaning commands write CSV files before the transaction.
Rerun `python setup_database.py` to verify that matching rows add no duplicates.

| Fresh-build table | Expected rows |
|---|---:|
| `artists` | 10,692 |
| `albums` | 22,543 |
| `genres` | 6 |
| `songs` | 28,352 |
| `song_genres` | 30,380 |
| `users` | 100 |
| `history` | 17,509 |
| `track_likes` | 729 |

`--skip-seed` creates/loads only the schema and catalog. `--data-dir DIR` selects
another normalized CSV directory. Matching existing catalog keys are preserved;
different values at an existing key abort the import. Setup does not reset data or
automatically migrate an incompatible schema.

## Synthetic listening data

After the catalog is loaded, the generator can also be run separately:

```bash
python seed_data.py
```

The generator creates 100 synthetic listeners and reproducible listening events using
seed 42, from May 1 through October 31, 2026 (UTC). It also creates up to eight
initial likes per listener ranked by qualifying plays with at least 80% completion.
Users without qualifying listens receive no initial likes.

The `generation_batches` table in `database/schema.sql` records the initial-likes
batch in the same transaction as its likes. Reruns leave existing users/history
unchanged and do not restore songs someone later unlikes. Existing manual likes
keep their timestamps. Conflicting synthetic users/history stop the import.

## Recommendations
Start the interface from the project folder:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Choose a listener and use three sections:

- **Listening:** minutes, plays, unique songs, a blue daily activity calendar,
  and your top five songs. Date filters, extra charts, and searchable history
  are tucked into expandable sections. Dates include both days in UTC;
  plays include partial listens.
- **Discover:** click **Find songs** for ten recommendations based on listening
  history. **Options** lets you change the method or number of songs.
  New listeners receive popular picks. Songs heard before generation are excluded.
  Popular unheard songs fill remaining spots when genre matches run out; lists
  are shorter only when fewer unheard songs remain than requested.
  Results save automatically; older results are under **Saved lists**, and scores
  and cutoffs are under **Details**.
- **Likes:** search your saved songs. Outlined hearts save songs; red hearts
  remove them. Changes persist immediately. **Undo** restores the most recent
  removal for two minutes, until another like/unlike action replaces it.

Hearts have song-specific accessible labels. Lists show ten rows per page.
**Daily activity details** provides a keyboard-accessible alternative to calendar
hover details; **Chart data** contains the extra charts' values. Multi-year ranges
have a calendar year selector. Analytics are cached for up to 60 seconds;
**Refresh** loads the latest data. Likes are independent of listening date filters
and do not change recommendation scores.

## Recommendation quality

Genre preferences now use each play's completion fraction (`listened_ms / duration_ms`),
so a fully heard long song does not outweigh a fully heard short song. Each play's
evidence is split across its genre labels, then candidate genre weights are averaged.
Unheard-song exclusion, popularity tie-breaking, and the no-history fallback are preserved.
Positive genre matches rank first; zero-score candidates fill remaining spots by
popularity, with track IDs breaking ties. Every song appears at most once.
The original duration-weighted query is retained in `queries/genre_baseline.sql` for comparison.

New genre lists use `genre_v2`; `popularity_v1` is unchanged. Existing `genre_v1`
lists retain their original ranks/scores and are labeled as earlier versions;
those lists may use either of the previous scoring rules.
`queries/recommend.py` generates and saves each run and its items in one transaction.
A failure leaves no partial run/items. Like/unlike does not feed recommendation scores.

**Existing database update:** open
[`database/migrations/001_genre_v2.sql`](database/migrations/001_genre_v2.sql)
in pgAdmin's Query Tool for the intended database and execute the whole file as
the table owner. For the shared project role, run `SET ROLE project_developers;`
first and `RESET ROLE;` afterward. This only expands the allowed method labels;
it preserves all saved lists and can be rerun. Fresh databases built with
`setup_database.py` already permit `genre_v2`. Pulling Git changes alone does
not update an existing database constraint.

Run the read-only comparison against an existing populated project database:

```bash
python -m evaluation.recommendations
# Or select another database explicitly:
python -m evaluation.recommendations --database-url "postgresql://localhost/music_recommendation"
```

The [measured report](evaluation/results/recommendation_quality.md) compares the original
genre ranker, completion weighting with popularity top-up, and popularity. It reports
Precision@K, Recall@K, NDCG@K, catalog coverage, full-list rate, and mean list length.
Quality metrics exclude listeners without relevant future targets; coverage and list
completeness include every evaluated listener. July–August and September–October
are fixed validation/test windows. Since the test period has already been inspected,
this rerun is a regression comparison, not a newly untouched test set. Synthetic
favorites are chosen randomly, so the report makes no claim of strong real-user accuracy.

Pipeline, recommendation, and analytics tests require an empty disposable local
database named `*_test`:

```bash
createdb music_pipeline_test
TEST_DATABASE_URL="postgresql://localhost/music_pipeline_test" python -m unittest discover -s tests -v
```

Database tests roll back their tables and data, including failed recommendation saves.
Pure normalization tests use temporary CSV directories; evaluation tests use
hand-calculated metrics and cutoff-boundary fixtures. Without `TEST_DATABASE_URL`, database tests are skipped;
a skipped run is not full verification.

## Analytics starter and index experiment

The [operations guide](docs/analytics-operations.md) defines five operation groups,
date boundaries, partial plays, and genre attribution. The standalone examples in
`queries/return_data.sql` are global; dashboard queries filter by listener and dates.

The [starter performance report](evaluation/results/physical_design.md) includes
before/after timings, query plans, and index storage costs for one listener/window.
To reproduce it on a separate local database:

```bash
createdb music_benchmark
python setup_database.py --database-url "postgresql://localhost/music_benchmark"
python -m evaluation.benchmark --database-url "postgresql://localhost/music_benchmark" --allow-index-changes
```

The benchmark rolls back candidate indexes. Broader workload experiments,
database-side pagination, and deployment decisions remain team follow-up work.
