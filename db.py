"""Shared PostgreSQL connection for the music recommendation project."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


def get_connection(*, read_only: bool = False) -> psycopg.Connection:
    """Open a connection; use it in a with block to close it automatically."""
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL in the project .env file.")

    conn = psycopg.connect(
        database_url,
        connect_timeout=15,
        application_name="music_recommendation",
    )
    conn.read_only = read_only
    return conn
