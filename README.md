# Music Recommendations and Listening History

Music catalog preparation and a shared PostgreSQL connection for analytics and
recommendations.

## Local setup

From the project directory, create a Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If `.env` does not exist yet, copy `.env.example` to `.env` and set `DATABASE_URL`
to the PostgreSQL connection URL from Neon's **Connect** dialog. Keep its SSL
parameters. `.env` and `.venv` are ignored by Git; each teammate supplies their
own local credentials. An existing `DATABASE_URL` environment variable takes
precedence over `.env`.

Verify the connection:

```bash
python check_connection.py
```

This runs a read-only transaction, prints the connected database and role,
checks TLS, and lists tables accessible to that role.

## Use the database in Python

```python
from db import get_connection

with get_connection(read_only=True) as conn:
    database = conn.execute("SELECT current_database()").fetchone()[0]
    print(database)
```

Use `get_connection()` for operations that need writes. The context manager
commits successful transactions, rolls back on exceptions, and closes the
connection. Use SQL parameters when passing user input into queries.

## Prepare the catalog CSVs

```bash
python cleaning_scripts/cleaned_spotify.py
python cleaning_scripts/normalize_spotify.py
```

These scripts write CSVs under `data/cleaned_data/`. Database loading is a
separate pipeline step.
