"""Create a compatible schema and import the catalog without overwriting data."""
import argparse
import csv
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from db import get_connection

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "cleaned_data"
CATALOG = {
    "artists": ("artist_id", "artist_name"),
    "genres": ("genre_id", "genre_name"),
    "albums": ("album_id", "album_name", "release_date", "artist_id"),
    "songs": ("track_id", "track_name", "artist_id", "track_popularity", "album_id", "genre_id", "subgenre", "duration_ms"),
    "song_genres": ("track_id", "genre_id"),
}
PRIMARY_KEYS = {
    "artists": ("artist_id",), "genres": ("genre_id",), "albums": ("album_id",),
    "songs": ("track_id",), "song_genres": ("track_id", "genre_id"), "users": ("user_id",),
    "history": ("history_id",), "track_likes": ("user_id", "track_id"),
    "generation_batches": ("batch_id",), "recommendation_runs": ("run_id",),
    "recommendation_items": ("run_id", "track_id"),
}
# Required column types are intentionally independent of varchar length, so the
# existing Neon schema's shorter track-id columns remain compatible.
REQUIRED_COLUMNS = {
    "artists": {"artist_id": "integer", "artist_name": "text"},
    "genres": {"genre_id": "integer", "genre_name": "character varying"},
    "albums": {"album_id": "character varying", "album_name": "text", "release_date": "character varying", "artist_id": "integer"},
    "songs": {"track_id": "character varying", "track_name": "character varying", "artist_id": "integer", "track_popularity": "integer", "album_id": "character varying", "genre_id": "integer", "subgenre": "text", "duration_ms": "integer"},
    "song_genres": {"track_id": "character varying", "genre_id": "integer"},
    "users": {"user_id": "integer", "username": "character varying"},
    "history": {"user_id": "integer", "track_id": "character varying", "played_when": "timestamp with time zone", "history_id": "bigint", "listened_ms": "integer"},
    "track_likes": {"user_id": "integer", "track_id": "character varying", "liked_when": "timestamp with time zone"},
    "generation_batches": {"batch_id": "text", "generated_at": "timestamp with time zone", "likes_count": "integer"},
    "recommendation_runs": {"run_id": "bigint", "user_id": "integer", "method": "text", "generated_at": "timestamp with time zone", "history_cutoff": "timestamp with time zone", "requested_k": "integer"},
    "recommendation_items": {"run_id": "bigint", "track_id": "character varying", "rank": "integer", "score": "numeric"},
}
FOREIGN_KEYS = {
    ("albums", ("artist_id",), "artists", ("artist_id",)),
    ("songs", ("artist_id",), "artists", ("artist_id",)),
    ("songs", ("album_id",), "albums", ("album_id",)),
    ("songs", ("genre_id",), "genres", ("genre_id",)),
    ("song_genres", ("track_id",), "songs", ("track_id",)),
    ("song_genres", ("genre_id",), "genres", ("genre_id",)),
    ("history", ("user_id",), "users", ("user_id",)),
    ("history", ("track_id",), "songs", ("track_id",)),
    ("track_likes", ("user_id",), "users", ("user_id",)),
    ("track_likes", ("track_id",), "songs", ("track_id",)),
    ("recommendation_runs", ("user_id",), "users", ("user_id",)),
    ("recommendation_items", ("run_id",), "recommendation_runs", ("run_id",)),
    ("recommendation_items", ("track_id",), "songs", ("track_id",)),
}


def validate_schema(conn):
    """Reject incompatible existing tables instead of silently accepting them."""
    columns = conn.execute("""SELECT table_name, column_name, data_type, is_nullable, is_identity
        FROM information_schema.columns WHERE table_schema = 'public'""").fetchall()
    found = {(table, column): (kind, nullable, identity) for table, column, kind, nullable, identity in columns}
    for table, spec in REQUIRED_COLUMNS.items():
        for column, kind in spec.items():
            actual = found.get((table, column))
            if actual is None or actual[0] != kind or actual[1] != "NO":
                raise ValueError(f"Incompatible schema: {table}.{column} must be non-null {kind}; use a fresh database or an explicit migration.")
    for table, column in [("history", "history_id"), ("recommendation_runs", "run_id")]:
        if found[table, column][2] != "YES":
            raise ValueError(f"Incompatible schema: {table}.{column} must be an identity column.")
    constraints = conn.execute("""SELECT t.relname, c.contype,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num, ord)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num ORDER BY k.ord),
        p.relname,
        ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num, ord)
              JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num ORDER BY k.ord),
        c.convalidated
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        LEFT JOIN pg_class p ON p.oid = c.confrelid
        WHERE n.nspname = 'public'""").fetchall()
    keys = {(t, kind, tuple(cols)) for t, kind, cols, _, _, valid in constraints if valid}
    for table, key in PRIMARY_KEYS.items():
        if (table, "p", key) not in keys:
            raise ValueError(f"Incompatible schema: missing primary key on {table}{key}.")
    if ("recommendation_items", "u", ("run_id", "rank")) not in keys:
        raise ValueError("Incompatible schema: recommendation ranks must be unique per run.")
    foreign = {(t, tuple(cols), p, tuple(parent_cols)) for t, kind, cols, p, parent_cols, valid in constraints if kind == "f" and valid}
    if not FOREIGN_KEYS <= foreign:
        raise ValueError(f"Incompatible schema: missing foreign keys: {sorted(FOREIGN_KEYS - foreign)}")
    for table, minimum in {"albums": 1, "songs": 2, "history": 1, "generation_batches": 1,
                           "recommendation_runs": 2, "recommendation_items": 2}.items():
        if sum(t == table and kind == "c" and valid for t, kind, _, _, _, valid in constraints) < minimum:
            raise ValueError(f"Incompatible schema: missing validated CHECK constraints on {table}.")


def initialize_schema(conn):
    """Apply portable DDL transactionally; never drop or rewrite existing data."""
    with conn.transaction():
        conn.execute((BASE_DIR / "database" / "schema.sql").read_text(), prepare=False)
        validate_schema(conn)


def import_catalog(conn, data_dir=DATA_DIR):
    """Insert absent catalog keys; any changed existing value aborts the import.

    Every source is staged and validated before insertion. The table locks keep
    conflict detection and insertion atomic even against other catalog writers.
    The caller can wrap this function in a larger transaction for a full rebuild.
    """
    data_dir = Path(data_dir)
    added = {}
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(341, 101)")
        conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
            sql.SQL(", ").join(sql.Identifier("public", table) for table in CATALOG)))
        staged = {}
        for table, columns in CATALOG.items():
            source = data_dir / f"{table}.csv"
            with source.open(newline="", encoding="utf-8") as stream:
                header = next(csv.reader(stream), None)
            if header != list(columns):
                raise ValueError(f"Unexpected columns in {source.name}: expected {list(columns)}; rerun normalization.")
            stage = f"catalog_{table}_{uuid4().hex}"
            staged[table] = stage
            conn.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING ALL) ON COMMIT DROP").format(
                sql.Identifier(stage), sql.Identifier("public", table)))
            with source.open("rb") as stream, conn.cursor() as cur:
                with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER true)").format(
                    sql.Identifier(stage), sql.SQL(", ").join(map(sql.Identifier, columns)))) as copy:
                    while chunk := stream.read(65536):
                        copy.write(chunk)
            if not conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(stage))).fetchone()[0]:
                raise ValueError(f"Empty catalog source: {source.name}")
            match = sql.SQL(" AND ").join(
                sql.SQL("t.{} = s.{}").format(sql.Identifier(key), sql.Identifier(key)) for key in PRIMARY_KEYS[table])
            values = [column for column in columns if column not in PRIMARY_KEYS[table]]
            if values:
                different = sql.SQL(" OR ").join(
                    sql.SQL("t.{} IS DISTINCT FROM s.{}").format(sql.Identifier(column), sql.Identifier(column)) for column in values)
                conflict = conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {} t JOIN {} s ON {} WHERE {})").format(
                    sql.Identifier("public", table), sql.Identifier(stage), match, different)).fetchone()[0]
                if conflict:
                    raise ValueError(f"Conflicting catalog data in {table}; no imported rows were committed. Use a fresh database or an explicit migration.")
        # A valid foreign key alone does not ensure that every song has a tag.
        # Require the exported primary pair as well as preserving other labels.
        missing_primary = conn.execute(sql.SQL("""SELECT EXISTS (
            SELECT 1 FROM {} s
            LEFT JOIN {} sg ON sg.track_id = s.track_id AND sg.genre_id = s.genre_id
            WHERE sg.track_id IS NULL
        )""").format(sql.Identifier(staged["songs"]), sql.Identifier(staged["song_genres"]))).fetchone()[0]
        if missing_primary:
            raise ValueError("Incomplete song_genres.csv: every song must include its primary genre association.")
        for table, columns in CATALOG.items():
            column_sql = sql.SQL(", ").join(map(sql.Identifier, columns))
            result = conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO NOTHING").format(
                sql.Identifier("public", table), column_sql, column_sql, sql.Identifier(staged[table]),
                sql.SQL(", ").join(map(sql.Identifier, PRIMARY_KEYS[table]))))
            added[table] = result.rowcount
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Explicit target URL; otherwise use DATABASE_URL/.env.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--skip-seed", action="store_true", help="Create/load the catalog without synthetic users/history/likes.")
    args = parser.parse_args()
    from seed_data import seed
    connection = psycopg.connect(args.database_url, connect_timeout=15) if args.database_url else get_connection()
    with connection as conn:
        with conn.transaction():
            initialize_schema(conn)
            counts = import_catalog(conn, args.data_dir)
            seed_counts = seed(conn) if not args.skip_seed else {}
    for table, count in counts.items():
        print(f"{table}: {count} new rows")
    for name, count in seed_counts.items():
        print(f"synthetic {name}: {count} new rows")
    print("Database setup complete; existing matching rows were preserved.")


if __name__ == "__main__":
    main()
