"""Verify the connection and list accessible tables without changing data."""

from db import get_connection


def main() -> None:
    with get_connection(read_only=True) as conn:
        database, role = conn.execute(
            "SELECT current_database(), current_user"
        ).fetchone()
        # Inspect this client's connection, including when PostgreSQL is
        # behind a proxy that terminates TLS.
        ssl = conn.pgconn.ssl_in_use
        tables = conn.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        ).fetchall()

    print(f"Connected successfully to {database} as {role}.")
    print(f"TLS encrypted: {bool(ssl)}")
    print(f"Accessible tables: {len(tables)}")
    for schema, table in tables:
        print(f"  {schema}.{table}")


if __name__ == "__main__":
    main()
