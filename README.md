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

## Recommendations
Start the interface from the project folder:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```
