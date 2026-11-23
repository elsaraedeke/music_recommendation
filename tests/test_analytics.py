"""Hand-calculated checks of the dashboard's actual SQL, without its UI cache.

Use an empty localhost database ending in ``_test`` via TEST_DATABASE_URL.
Fixtures, schema creation, and all rows are rolled back after each test.
"""

import os
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import psycopg
from psycopg.conninfo import conninfo_to_dict


START = datetime(2026, 5, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 3, tzinfo=timezone.utc)


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("Set TEST_DATABASE_URL to an empty localhost database ending in _test.")
        info = conninfo_to_dict(cls.database_url)
        if info.get("host") not in {"localhost", "127.0.0.1", "::1"} or not info.get("dbname", "").endswith("_test"):
            raise RuntimeError("Analytics tests require an explicit localhost *_test database.")
        if any(key in info for key in ("hostaddr", "service")) or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE")):
            raise RuntimeError("Do not use hostaddr/service overrides for analytics tests.")
        with psycopg.connect(cls.database_url, connect_timeout=10) as conn:
            if conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
                raise RuntimeError("Use an empty test database; existing tables will never be dropped.")

    def setUp(self):
        import app
        from setup_database import initialize_schema

        self.conn = psycopg.connect(self.database_url, connect_timeout=10)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.conn.execute("BEGIN")
        initialize_schema(self.conn)
        self.conn.execute("""
            INSERT INTO public.artists VALUES (1,'Alpha'),(2,'Beta');
            INSERT INTO public.genres VALUES (1,'pop'),(2,'rock');
            INSERT INTO public.albums VALUES ('album','Fixture','2020',1);
            INSERT INTO public.users VALUES (1,'listener'),(2,'other listener');
            INSERT INTO public.songs VALUES
                ('multi','Multi',1,80,'album',1,'fixture',120000),
                ('single','Single',2,60,'album',1,'fixture',180000),
                ('untagged','Untagged',1,40,'album',1,'fixture',90000);
            INSERT INTO public.song_genres VALUES ('multi',1),('multi',2),('single',1);
        """, prepare=False)
        with self.conn.cursor() as cur:
            cur.executemany("""INSERT INTO public.history
                (user_id,track_id,played_when,listened_ms) VALUES (%s,%s,%s,%s)""", [
                (1, 'multi', '2026-04-30T23:59:59.999Z', 120000),
                (1, 'multi', '2026-05-01T00:00:00Z', 90000),
                (1, 'single', '2026-05-01T03:00:00Z', 30000),
                (1, 'multi', '2026-05-03T12:00:00Z', 60000),
                (1, 'single', '2026-05-31T23:59:59Z', 60000),
                (1, 'untagged', '2026-06-01T00:00:00Z', 30000),
                (1, 'multi', '2026-06-02T23:59:59.999Z', 120000),
                (1, 'single', '2026-06-03T00:00:00Z', 180000),
                (2, 'multi', '2026-05-01T12:00:00Z', 120000),
            ])
        # The helper's context manager must not commit/close our fixture connection.
        connection_patch = patch.object(app, "get_connection", side_effect=lambda **kwargs: nullcontext(self.conn))
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        self.listening_data = app.listening_data.__wrapped__

    def test_fractional_totals_and_genre_minutes_do_not_double_count(self):
        totals, daily, genres = self.listening_data(1, START, END)
        self.assertEqual(tuple(map(float, totals)), (6.5, 6.0, 3.0, 2.0))
        self.assertAlmostEqual(daily["Minutes"].sum(), 6.5)
        shares = genres.set_index("Genre")["Minutes"].to_dict()
        self.assertEqual(shares, {"pop": 3.75, "rock": 2.25, "Uncategorized": 0.5})
        self.assertAlmostEqual(sum(shares.values()), float(totals[0]))

    def test_daily_zero_fill_and_monthly_chart_preserve_totals(self):
        _, daily, _ = self.listening_data(1, START, END)
        self.assertEqual(len(daily), 33)
        self.assertEqual(daily.loc[pd.Timestamp("2026-05-02"), "Minutes"], 0)
        self.assertEqual(daily.loc[pd.Timestamp("2026-05-01"), "Events"], 2)
        monthly = daily.resample("MS").sum()
        self.assertEqual(monthly["Minutes"].tolist(), [4.0, 2.5])
        self.assertEqual(monthly["Events"].tolist(), [4, 2])

    def test_song_artist_rankings_and_history_match_actual_listened_time(self):
        songs, artists = self.listening_data(1, START, END, "Top Music")
        self.assertEqual(songs["Track ID"].tolist(), ["multi", "single", "untagged"])
        self.assertEqual(songs["Plays"].tolist(), [3, 2, 1])
        self.assertEqual(songs["Minutes"].tolist(), [4.5, 1.5, 0.5])
        self.assertEqual(artists["Artist"].tolist(), ["Alpha", "Beta"])
        self.assertEqual(artists["Plays"].tolist(), [4, 2])
        self.assertEqual(artists["Minutes"].tolist(), [5.0, 1.5])
        history = self.listening_data(1, START, END, "History")
        self.assertEqual(len(history), 6)
        self.assertEqual(history["Track ID"].tolist(), ["multi", "untagged", "single", "multi", "single", "multi"])
        self.assertEqual(history.iloc[0]["Completion (%)"], 100.0)
        self.assertEqual(history.iloc[-1]["Completion (%)"], 75.0)

    def test_utc_boundaries_and_empty_range(self):
        end = datetime(2026, 5, 2, tzinfo=timezone.utc)
        totals, _, _ = self.listening_data(1, START, end)
        self.assertEqual(tuple(map(float, totals)), (2.0, 2.0, 2.0, 2.0))
        history = self.listening_data(1, START, END, "History")
        self.assertEqual(history.iloc[-1]["Played at (UTC)"], datetime(2026, 5, 1))
        self.assertLess(history.iloc[0]["Played at (UTC)"], END.replace(tzinfo=None))
        empty_start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        empty_end = datetime(2026, 4, 2, tzinfo=timezone.utc)
        totals, daily, genres = self.listening_data(1, empty_start, empty_end)
        self.assertEqual(tuple(map(float, totals)), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(daily["Minutes"].sum(), 0)
        self.assertTrue(genres.empty)
        songs, artists = self.listening_data(1, empty_start, empty_end, "Top Music")
        self.assertTrue(songs.empty and artists.empty)
        self.assertTrue(self.listening_data(1, empty_start, empty_end, "History").empty)

    def test_standalone_examples_preserve_fractional_minutes_and_play_attribution(self):
        query_file = Path(__file__).resolve().parents[1] / "queries" / "return_data.sql"
        results = []
        with self.conn.cursor() as cur:
            cur.execute(query_file.read_text(), prepare=False)
            while True:
                columns = [column.name for column in cur.description]
                results.append([dict(zip(columns, row)) for row in cur.fetchall()])
                if not cur.nextset():
                    break
        self.assertEqual(len(results), 6)
        self.assertEqual(float(results[0][0]["total_min"]), 13.5)
        self.assertAlmostEqual(sum(float(row["attributed_plays"]) for row in results[-1]), 9.0)
        self.assertAlmostEqual(sum(float(row["pct_of_plays"]) for row in results[-1]), 100.0, delta=0.02)


if __name__ == "__main__":
    unittest.main()
