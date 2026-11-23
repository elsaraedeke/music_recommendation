"""Recommendation integration checks against an empty disposable local database.

TEST_DATABASE_URL must name a localhost database ending in ``_test``. Each test
creates its tiny catalog inside a transaction and rolls back all tables and rows.
No project .env connection is used.
"""

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
CUTOFF = datetime(2026, 9, 1, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE public.artists (artist_id integer PRIMARY KEY, artist_name text NOT NULL);
CREATE TABLE public.genres (genre_id integer PRIMARY KEY, genre_name text NOT NULL);
CREATE TABLE public.users (user_id integer PRIMARY KEY, username text NOT NULL);
CREATE TABLE public.songs (
    track_id varchar(300) PRIMARY KEY, track_name text NOT NULL,
    artist_id integer NOT NULL REFERENCES public.artists, track_popularity integer NOT NULL,
    album_id text NOT NULL, genre_id integer NOT NULL REFERENCES public.genres,
    subgenre text NOT NULL, duration_ms integer NOT NULL CHECK (duration_ms > 0)
);
CREATE TABLE public.song_genres (
    track_id varchar(300) REFERENCES public.songs, genre_id integer REFERENCES public.genres,
    PRIMARY KEY (track_id, genre_id)
);
CREATE TABLE public.history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id integer NOT NULL REFERENCES public.users, track_id varchar(300) NOT NULL REFERENCES public.songs,
    played_when timestamptz NOT NULL, listened_ms integer NOT NULL CHECK (listened_ms >= 0)
);
CREATE TABLE public.track_likes (
    user_id integer REFERENCES public.users, track_id varchar(300) REFERENCES public.songs,
    liked_when timestamptz NOT NULL, PRIMARY KEY (user_id, track_id)
);
INSERT INTO public.artists VALUES (1, 'Fixture artist');
INSERT INTO public.genres VALUES (1, 'pop'), (2, 'rock'), (3, 'jazz');
INSERT INTO public.users VALUES (1, 'active listener'), (2, 'new listener');
"""


class RecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("Set TEST_DATABASE_URL to an empty local database ending in _test.")
        details = conninfo_to_dict(cls.database_url)
        if details.get("host") not in {"localhost", "127.0.0.1", "::1"} or not details.get("dbname", "").endswith("_test"):
            raise RuntimeError("Recommendation tests require an explicit localhost *_test database.")
        if any(key in details for key in ("hostaddr", "service")) or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE")):
            raise RuntimeError("Do not use hostaddr/service overrides for recommendation tests.")
        with psycopg.connect(cls.database_url, connect_timeout=10) as conn:
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            if tables:
                raise RuntimeError("Use an empty test database; existing tables will never be dropped.")

    def setUp(self):
        self.conn = psycopg.connect(self.database_url, connect_timeout=10)
        self.conn.execute(SCHEMA, prepare=False)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def song(self, track, genres=(1,), duration=180000, popularity=50):
        self.conn.execute("""INSERT INTO public.songs VALUES
            (%s,%s,1,%s,'fixture_album',%s,'fixture_subgenre',%s)""",
            (track, track, popularity, genres[0], duration))
        with self.conn.cursor() as cur:
            cur.executemany("INSERT INTO public.song_genres VALUES (%s,%s)", [(track, genre) for genre in genres])

    def listen(self, track, listened=180000, played="2026-08-01T12:00:00Z", user=1):
        self.conn.execute("""INSERT INTO public.history(user_id,track_id,played_when,listened_ms)
            VALUES (%s,%s,%s,%s)""", (user, track, played, listened))

    def recommend(self, method="genre", user=1, cutoff=CUTOFF, k=10):
        query = (ROOT / "queries" / f"{method}_candidates.sql").read_text()
        return self.conn.execute(query, {"user_id": user, "cutoff": cutoff, "k": k}).fetchall()

    def test_cold_start_matches_popularity_and_unknown_user_has_no_results(self):
        self.song("popular", popularity=99)
        self.song("other", genres=(2,), popularity=10)
        self.assertEqual(self.recommend(user=2), self.recommend("popularity", user=2))
        self.assertEqual(self.recommend(user=999), [])

    def test_excludes_only_history_before_cutoff_and_is_deterministic(self):
        for track in ("heard", "future", "unheard", "another"):
            self.song(track)
        self.listen("heard")
        self.listen("future", played="2026-09-02T00:00:00Z")
        rows = self.recommend(k=3)
        tracks = [row[0] for row in rows]
        self.assertNotIn("heard", tracks)
        self.assertIn("future", tracks)
        self.assertEqual(len(tracks), len(set(tracks)))
        self.assertLessEqual(len(tracks), 3)
        self.assertEqual(rows, self.recommend(k=3))
        self.assertEqual(self.recommend(k=2), rows[:2])

    def test_future_history_does_not_change_scores_or_rankings(self):
        self.song("heard", genres=(1,))
        self.song("candidate", genres=(1,))
        self.song("other_genre", genres=(2,))
        self.listen("heard")
        before = self.recommend()
        self.listen("other_genre", played="2026-09-03T12:00:00Z")
        self.assertEqual(before, self.recommend())

    def test_equivalent_timezone_cutoffs_return_identical_results(self):
        self.song("heard")
        self.song("candidate")
        self.listen("heard", played="2026-08-31T23:30:00Z")
        offset_cutoff = datetime.fromisoformat("2026-08-31T20:00:00-04:00")
        self.assertEqual(self.recommend(cutoff=CUTOFF), self.recommend(cutoff=offset_cutoff))

    def test_zero_time_history_falls_back_without_recommending_heard_track(self):
        self.song("heard")
        self.song("candidate", popularity=90)
        self.song("another", genres=(2,), popularity=10)
        self.listen("heard", listened=0)
        self.assertEqual(self.recommend(), self.recommend("popularity"))
        self.assertNotIn("heard", {row[0] for row in self.recommend()})

    def test_full_short_and_long_songs_give_equal_preference_evidence(self):
        self.song("heard_short", genres=(1,), duration=60000)
        self.song("heard_long", genres=(2,), duration=600000)
        self.song("candidate_short_genre", genres=(1,))
        self.song("candidate_long_genre", genres=(2,))
        self.listen("heard_short", listened=60000)
        self.listen("heard_long", listened=600000)
        scores = {row[0]: row[3] for row in self.recommend()}
        self.assertEqual(scores["candidate_short_genre"], scores["candidate_long_genre"])

    def test_completion_ratio_outweighs_raw_listening_duration(self):
        self.song("half_short", genres=(1,), duration=60000)
        self.song("tenth_long", genres=(2,), duration=600000)
        self.song("candidate_half", genres=(1,))
        self.song("candidate_tenth", genres=(2,))
        self.listen("half_short", listened=30000)
        self.listen("tenth_long", listened=60000)
        scores = {row[0]: row[3] for row in self.recommend()}
        self.assertGreater(scores["candidate_half"], scores["candidate_tenth"])

    def test_multi_genre_play_splits_its_preference_evidence(self):
        self.song("heard_multi", genres=(1, 2))
        self.song("heard_single", genres=(3,))
        self.song("candidate_one", genres=(1,))
        self.song("candidate_two", genres=(2,))
        self.song("candidate_three", genres=(3,))
        self.listen("heard_multi")
        self.listen("heard_single")
        scores = {row[0]: row[3] for row in self.recommend()}
        self.assertEqual(scores["candidate_one"], scores["candidate_two"])
        self.assertAlmostEqual(float(scores["candidate_three"]), 2 * float(scores["candidate_one"]))


if __name__ == "__main__":
    unittest.main()
