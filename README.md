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

See [the data pipeline guide](docs/data-pipeline.md) for source verification,
transformation rules, validation, and troubleshooting.

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
The original duration-weighted query is retained in `queries/genre_baseline.sql` for comparison.

Existing database tables and method identifiers are compatible. Saved lists retain
their original ranks/scores; earlier lists may use the previous duration-based rule.
Like/unlike behavior is unchanged and does not feed recommendation scores.

Run the read-only comparison against an existing populated project database:

```bash
python -m evaluation.recommendations
# Or select another database explicitly:
python -m evaluation.recommendations --database-url "postgresql://localhost/music_recommendation"
```

The [measured report](evaluation/results/recommendation_quality.md) compares the original
genre ranker, completion weighting, and popularity. Model selection used July–August;
September–October was evaluated after the scoring rule was frozen. Validation improved
modestly, while the final exact-track hit count did not improve. Synthetic favorites
are chosen randomly, so the report makes no claim of strong real-user accuracy.

Pipeline and recommendation regression tests require an empty disposable local
database named `*_test`:

```bash
createdb music_pipeline_test
TEST_DATABASE_URL="postgresql://localhost/music_pipeline_test" python -m unittest discover -s tests -v
```

Database tests roll back their tables and data. Pure normalization tests use
temporary CSV directories. Without `TEST_DATABASE_URL`, database tests are skipped;
a skipped run is not full verification.
