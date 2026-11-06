import random
from datetime import datetime, timedelta, timezone

from db import get_connection

FIRST_ID = 341100
START = datetime(2026, 5, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


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


def seed():
    with get_connection() as conn:
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
            print(f"Already loaded: {len(users)} users and {len(events)} events. Nothing added.")
            return
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO public.users (user_id, username) VALUES (%s, %s)", users)
            with cur.copy("COPY public.history (user_id, track_id, played_when, listened_ms) FROM STDIN") as copy:
                for event in events:
                    copy.write_row(event)
    print(f"Added {len(users)} users and {len(events)} listening events.")


if __name__ == "__main__":
    seed()
