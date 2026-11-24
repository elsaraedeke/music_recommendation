"""Generate and save a recommendation list atomically."""

from datetime import datetime
from pathlib import Path


METHOD_VERSIONS = {"genre": "genre_v2", "popularity": "popularity_v1"}


def save_recommendations(conn, user_id, method, cutoff, k):
    """Return the run ID; roll back the run and every item if any step fails.

    Joins an existing transaction using a savepoint, or commits its own transaction
    when the connection is idle. The caller owns and closes the connection.
    """
    if method not in METHOD_VERSIONS:
        raise ValueError("Unknown recommendation method")
    if type(k) is not int or not 1 <= k <= 50:
        raise ValueError("Number of songs must be an integer from 1 to 50")
    if not isinstance(cutoff, datetime) or cutoff.utcoffset() is None:
        raise ValueError("Recommendation cutoff must include a timezone")
    query = Path(__file__).with_name(f"{method}_candidates.sql").read_text()
    with conn.transaction():
        rows = conn.execute(query, {"user_id": user_id, "cutoff": cutoff, "k": k}).fetchall()
        run_id = conn.execute("""INSERT INTO public.recommendation_runs
            (user_id, method, history_cutoff, requested_k)
            VALUES (%s, %s, %s, %s) RETURNING run_id""",
            (user_id, METHOD_VERSIONS[method], cutoff, k)).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO public.recommendation_items
                (run_id, track_id, rank, score) VALUES (%s, %s, %s, %s)""",
                [(run_id, row[0], rank, row[3]) for rank, row in enumerate(rows, 1)])
    return run_id
