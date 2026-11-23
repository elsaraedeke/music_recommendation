# Music Recommendations and Listening History

## Local setup

Create a Python environment and install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `DATABASE_URL` in `.env` using your own Neon credentials.

Verify the connection:
```bash
python check_connection.py
```

## Use the database in Python

```python
from db import get_connection

with get_connection(read_only=True) as conn:
    database = conn.execute("SELECT current_database()").fetchone()[0]
    print(database)
```

## Prepare the catalog CSVs

```bash
python cleaning_scripts/cleaned_spotify.py
python cleaning_scripts/normalize_spotify.py
```

## Synthetic listening data

```bash
python seed_data.py
```

The generator creates 100 synthetic listeners and reproducible listening events using
seed 42, from May 1 through October 31, 2026 (UTC). It also creates up to eight
initial likes per listener from repeated plays with at least 80% completion.
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

Focused regression tests require an empty disposable local database named `*_test`:

```bash
createdb music_quality_test
TEST_DATABASE_URL="postgresql://localhost/music_quality_test" python -m unittest discover -s tests -v
```

These tests roll back their tables and data. Without `TEST_DATABASE_URL`, database
tests are skipped; a skipped run is not full verification.
