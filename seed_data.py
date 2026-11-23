import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from db import get_connection

FIRST_ID = 341100
START = datetime(2026, 5, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)
LIKES_BATCH_ID = "synthetic_v1_likes"


def generate(catalog):
    rng = random.Random(42)
    counts = [rng.randint(100, 300) for _ in range(90)] + [rng.randint(1, 5) for _ in range(5)] + [0] * 5
    users = [(FIRST_ID + i, f"synthetic_v1_{i + 1:03d}") for i in range(len(counts))]
    genres = sorted({g for _, _, tags in catalog for g in tags})
    slot_seconds = max(duration for _, duration, _ in catalog) // 1000 + 61
    slots = range(int((END - START).total_seconds()) // slot_seconds)
    events = []
    for (user_id, _), count in zip(users, counts):
        if not count:
            continue
        preferred_genres = set(rng.sample(genres, rng.randint(1, min(2, len(genres)))))
        preferred = [(track, duration) for track, duration, tags in catalog if preferred_genres.intersection(tags)]
        other = [(track, duration) for track, duration, tags in catalog if not preferred_genres.intersection(tags)]
        favorites = rng.sample(preferred, min(10, len(preferred)))
        offset = rng.randrange(60)
        for slot in sorted(rng.sample(slots, count)):
            choice = rng.random()
            pool = favorites if choice < 0.4 else preferred if choice < 0.75 else (other or preferred)
            track_id, duration = rng.choice(pool)
            listened = duration if rng.random() < 0.8 else int(duration * rng.uniform(0.1, 0.8))
            played_when = START + timedelta(seconds=slot * slot_seconds + offset)
            events.append((user_id, track_id, played_when, listened))
    return users, events


def generate_likes(events, catalog):
    durations = {track: duration for track, duration, _ in catalog}
    favorites = {}
    for user_id, track_id, played_when, listened in events:
        duration = durations[track_id]
        finished = played_when + timedelta(milliseconds=listened)
        if listened * 5 < duration * 4 or finished >= END:
            continue
        plays, full_plays, last = favorites.get((user_id, track_id), (0, 0, finished))
        favorites[user_id, track_id] = (plays + 1, full_plays + (listened == duration), max(last, finished))
    counts = defaultdict(int)
    likes = []
    for (user_id, track_id), (_, _, liked_when) in sorted(
        favorites.items(), key=lambda item: (item[0][0], -item[1][0], -item[1][1], item[0][1])
    ):
        if counts[user_id] < 8:
            likes.append((user_id, track_id, liked_when))
            counts[user_id] += 1
    return likes


def seed(conn=None):
    """Seed atomically, optionally sharing setup's existing transaction."""
    if conn is None:
        with get_connection() as connection:
            return _seed(connection)
    return _seed(conn)


def _seed(conn):
    added_users = added_events = added_likes = 0
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(341, 100)")
        catalog = conn.execute(
            """SELECT s.track_id, s.duration_ms, array_agg(sg.genre_id ORDER BY sg.genre_id)
               FROM public.songs s JOIN public.song_genres sg ON sg.track_id = s.track_id
               GROUP BY s.track_id ORDER BY s.track_id COLLATE "C";"""
        ).fetchall()
        if not catalog:
            raise ValueError("Load songs and song_genres before generating history.")
        users, events = generate(catalog)
        id_range = (users[0][0], users[-1][0])
        existing = conn.execute(
            """SELECT user_id, username FROM public.users
               WHERE user_id BETWEEN %s AND %s OR username = ANY(%s) ORDER BY user_id""",
            (*id_range, [name for _, name in users]),
        ).fetchall()
        if existing:
            history = conn.execute(
                """SELECT user_id, track_id, played_when, listened_ms FROM public.history
                   WHERE user_id BETWEEN %s AND %s""", id_range,
            ).fetchall()
            if existing != users or sorted(history) != sorted(events):
                raise ValueError("Conflicting or incomplete synthetic dataset. No data was changed.")
        else:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO public.users (user_id, username) VALUES (%s, %s)", users)
                with cur.copy("COPY public.history (user_id, track_id, played_when, listened_ms) FROM STDIN") as copy:
                    for event in events:
                        copy.write_row(event)
            added_users, added_events = len(users), len(events)
        if not conn.execute(
            "SELECT 1 FROM public.generation_batches WHERE batch_id = %s", (LIKES_BATCH_ID,)
        ).fetchone():
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO public.track_likes (user_id, track_id, liked_when)
                       VALUES (%s, %s, %s) ON CONFLICT (user_id, track_id) DO NOTHING""",
                    generate_likes(events, catalog),
                )
                added_likes = cur.rowcount
            conn.execute(
                "INSERT INTO public.generation_batches (batch_id, likes_count) VALUES (%s, %s)",
                (LIKES_BATCH_ID, added_likes),
            )
    return {"users": added_users, "history": added_events, "track_likes": added_likes}


if __name__ == "__main__":
    counts = seed()
    print(f"Added {counts['users']} users, {counts['history']} listening events, "
          f"and {counts['track_likes']} initial likes.")
