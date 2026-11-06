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

Adds 100 users (IDs 341100–341199): 90 active, 5 light, and 5 with no history.
Seed 42 generates May–October 2026 events with genre preferences, repeated favorites,
and partial listens. Users and events are inserted in one transaction; existing data
is preserved. Reruns skip an identical dataset and stop on conflicts or partial data.
Reproduction requires the same catalog. These deliberately generated preferences
help test the app; they do not demonstrate recommendation quality for real listeners.

## Recommendations

The recommendation tables already exist in shared Neon. Their definitions are
included in `database/schema.sql`; that schema export excludes ownership and grants.

Start the interface from the project folder:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Choose a listener and inclusive UTC date range in the sidebar. The tabs show
listening totals and monthly activity, top music and genre minutes, searchable
history, and recommendations. Genre minutes are split across each song's genres.

In Recommendations, choose a method and number of songs, then click **Generate & save**.
The app saves the run and songs together; use **Saved lists** to reopen older results.
Recommendations use all history before the end of the selected end date (up to now);
the start date only filters statistics and history. The app uses your existing `.env`.

Run either candidate query through Python (the `%()` placeholders are psycopg parameters):

```python
from datetime import datetime, timezone
from pathlib import Path
from db import get_connection

sql = Path("queries/genre_candidates.sql").read_text()
with get_connection(read_only=True) as conn:
    rows = conn.execute(sql, {
        "user_id": 341001, "cutoff": datetime.now(timezone.utc), "k": 10
    }).fetchall()
print(rows)
```

Use `popularity_candidates.sql` for catalog popularity. Genre scores average
normalized listening-time preferences across a candidate's genres; users without
usable history get popularity results. Both queries return up to `k` unique songs,
exclude songs heard strictly before `cutoff`, and return no rows for an unknown user.
Pass a positive integer for `k`. These queries only read data; saving a run and its
items should use one transaction. Catalog popularity is a fixed dataset snapshot.
