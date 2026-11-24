"""Pipeline checks; database tests roll back their schema and fixture data.

Set TEST_DATABASE_URL to an empty localhost database ending in _test. No project
.env connection is used, and existing tables are never removed by these tests.
"""

import csv
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.conninfo import conninfo_to_dict


def cleaned_fixture(directory):
    columns = ["track_id", "track_name", "track_artist", "track_popularity",
               "track_album_id", "track_album_name", "track_album_release_date",
               "playlist_genre", "playlist_subgenre", "duration_ms"]
    rows = [
        ("track_a", "Song A", "Artist A", 80, "album_a", "Album A", "2020", "pop", "pop subgenre", 60000),
        ("track_a", "Song A", "Artist A", 80, "album_a", "Album A", "2020", "rock", "rock subgenre", 60000),
        ("track_b", "Song B", "Artist A", 60, "album_a", "Album A", "2020", "pop", "pop subgenre", 120000),
        ("track_b", "Song B", "Artist A", 60, "album_a", "Album A", "2020", "pop", "pop subgenre", 120000),
        ("track_c", "Song C", "Artist B", 40, "album_b", "Album B", "2021-02", "rock", "rock subgenre", 240000),
        ("track_d", "Song D", "Artist A", 20, "album_a", "Album A", "2020", "pop", "pop subgenre", 180000),
    ]
    with (Path(directory) / "spotify_cleaned.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def prepare_csvs(directory):
    from cleaning_scripts.normalize_spotify import normalize
    cleaned_fixture(directory)
    return normalize(Path(directory))


class NormalizationTests(unittest.TestCase):
    def test_duration_all_genres_and_primary_genre_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            counts = prepare_csvs(directory)
            songs = pd.read_csv(Path(directory, "songs.csv")).set_index("track_id")
            pairs = pd.read_csv(Path(directory, "song_genres.csv"))
            self.assertEqual(counts["songs"], 4)
            self.assertEqual(counts["song_genres"], 5)
            self.assertEqual(songs.loc["track_a", "duration_ms"], 60000)
            self.assertEqual(songs.loc["track_c", "duration_ms"], 240000)
            self.assertEqual(songs.loc["track_a", "genre_id"], 1)
            self.assertEqual(songs.loc["track_a", "subgenre"], "pop subgenre")
            self.assertEqual(set(map(tuple, pairs.values)), {
                ("track_a", 1), ("track_a", 2), ("track_b", 1), ("track_c", 2), ("track_d", 1)})

    def test_repeated_normalization_is_byte_identical(self):
        from cleaning_scripts.normalize_spotify import normalize
        with tempfile.TemporaryDirectory() as directory:
            prepare_csvs(directory)
            outputs = [path for path in Path(directory).glob("*.csv") if path.name != "spotify_cleaned.csv"]
            first = {path.name: path.read_bytes() for path in outputs}
            normalize(Path(directory))
            self.assertEqual(first, {path.name: path.read_bytes() for path in outputs})

    def test_invalid_duration_preserves_previous_outputs(self):
        from cleaning_scripts.normalize_spotify import normalize
        with tempfile.TemporaryDirectory() as directory:
            prepare_csvs(directory)
            source = Path(directory, "spotify_cleaned.csv")
            outputs = [path for path in Path(directory).glob("*.csv") if path != source]
            before = {path.name: path.read_bytes() for path in outputs}
            invalid = pd.read_csv(source)
            invalid.loc[invalid["track_id"] == "track_a", "duration_ms"] = 0
            invalid.to_csv(source, index=False)
            with self.assertRaises(ValueError):
                normalize(Path(directory))
            self.assertEqual(before, {path.name: path.read_bytes() for path in outputs})


class PipelineDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("Set TEST_DATABASE_URL to an empty localhost database ending in _test.")
        info = conninfo_to_dict(cls.database_url)
        if info.get("host") not in {"localhost", "127.0.0.1", "::1"} or not info.get("dbname", "").endswith("_test"):
            raise RuntimeError("Pipeline tests require an explicit localhost *_test database.")
        if any(key in info for key in ("hostaddr", "service")) or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE")):
            raise RuntimeError("Do not use hostaddr/service overrides for pipeline tests.")
        with psycopg.connect(cls.database_url, connect_timeout=10) as conn:
            if conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
                raise RuntimeError("Use an empty test database; existing tables will never be dropped.")

    def setUp(self):
        self.conn = psycopg.connect(self.database_url, connect_timeout=10)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.conn.execute("BEGIN")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        prepare_csvs(self.data_dir)

    def load_catalog(self):
        from setup_database import import_catalog, initialize_schema
        initialize_schema(self.conn)
        return import_catalog(self.conn, self.data_dir)

    def load_listener(self):
        self.load_catalog()
        self.conn.execute("INSERT INTO public.users VALUES (1, 'listener')")

    def listen(self, track="track_a", listened=30000, user=1):
        return self.conn.execute("""INSERT INTO public.history
            (user_id,track_id,played_when,listened_ms)
            VALUES (%s,%s,'2026-08-01T12:00:00Z',%s) RETURNING history_id""",
            (user, track, listened)).fetchone()[0]

    def test_zero_partial_full_and_repeated_listens_are_valid(self):
        self.load_listener()
        ids = [self.listen(listened=duration) for duration in (0, 30000, 60000, 60000)]
        self.assertEqual(len(set(ids)), 4)
        self.conn.execute("UPDATE public.history SET listened_ms=60000 WHERE history_id=%s", (ids[1],))
        self.assertEqual(self.conn.execute("SELECT listened_ms FROM public.history ORDER BY history_id").fetchall(),
                         [(0,), (60000,), (60000,), (60000,)])

    def test_invalid_listening_insert_leaves_existing_events_unchanged(self):
        self.load_listener()
        existing = self.listen()
        for duration in (60001, -1):
            with self.subTest(duration=duration), self.assertRaises(psycopg.errors.CheckViolation) as error:
                with self.conn.transaction():
                    self.listen(listened=duration)
            self.assertEqual(error.exception.sqlstate, "23514")
        self.assertEqual(self.conn.execute("SELECT history_id,listened_ms FROM public.history").fetchall(),
                         [(existing, 30000)])

    def test_invalid_listening_updates_preserve_original_event(self):
        self.load_listener()
        event = self.listen("track_b", 90000)
        statements = ["UPDATE public.history SET listened_ms=120001 WHERE history_id=%s",
                      "UPDATE public.history SET track_id='track_a' WHERE history_id=%s"]
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.CheckViolation) as error:
                with self.conn.transaction():
                    self.conn.execute(statement, (event,))
            self.assertEqual(error.exception.sqlstate, "23514")
            self.assertEqual(self.conn.execute("SELECT track_id,listened_ms FROM public.history WHERE history_id=%s", (event,)).fetchone(),
                             ("track_b", 90000))
        self.conn.execute("UPDATE public.history SET track_id='track_c',listened_ms=240000 WHERE history_id=%s", (event,))
        self.assertEqual(self.conn.execute("SELECT track_id,listened_ms FROM public.history WHERE history_id=%s", (event,)).fetchone(),
                         ("track_c", 240000))

    def test_song_duration_edits_preserve_existing_listens(self):
        self.load_listener()
        self.listen("track_b", 90000)
        self.listen("track_b", 30000)
        with self.assertRaises(psycopg.errors.CheckViolation) as error:
            with self.conn.transaction():
                self.conn.execute("UPDATE public.songs SET duration_ms=89999 WHERE track_id='track_b'")
        self.assertEqual(error.exception.sqlstate, "23514")
        self.assertEqual(self.conn.execute("SELECT duration_ms FROM public.songs WHERE track_id='track_b'").fetchone()[0], 120000)
        for duration in (180000, 90000, 90000):
            self.conn.execute("UPDATE public.songs SET duration_ms=%s WHERE track_id='track_b'", (duration,))
        self.conn.execute("UPDATE public.songs SET duration_ms=1 WHERE track_id='track_d'")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute("UPDATE public.songs SET duration_ms=0 WHERE track_id='track_d'")
        self.assertEqual(self.conn.execute("SELECT duration_ms FROM public.songs WHERE track_id='track_d'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT listened_ms FROM public.history ORDER BY history_id").fetchall(), [(90000,), (30000,)])

    def test_duplicate_primary_and_unique_keys_are_rejected(self):
        self.load_listener()
        self.conn.execute("INSERT INTO public.track_likes VALUES (1,'track_a','2026-08-01T12:00:00Z')")
        run_id = self.conn.execute("""INSERT INTO public.recommendation_runs
            (user_id,method,history_cutoff,requested_k)
            VALUES (1,'popularity_v1','2026-09-01T00:00:00Z',2) RETURNING run_id""").fetchone()[0]
        self.conn.execute("INSERT INTO public.recommendation_items VALUES (%s,'track_a',1,80)", (run_id,))
        duplicates = [
            ("INSERT INTO public.users VALUES (1,'duplicate')", (), "users_pkey"),
            ("INSERT INTO public.track_likes VALUES (1,'track_a',CURRENT_TIMESTAMP)", (), "track_likes_pkey"),
            ("INSERT INTO public.recommendation_items VALUES (%s,'track_b',1,60)", (run_id,), "recommendation_items_run_id_rank_key"),
        ]
        for statement, parameters, constraint in duplicates:
            with self.subTest(constraint=constraint), self.assertRaises(psycopg.errors.UniqueViolation) as error:
                with self.conn.transaction():
                    self.conn.execute(statement, parameters)
            self.assertEqual(error.exception.diag.constraint_name, constraint)
        self.assertEqual(self.conn.execute("SELECT username FROM public.users WHERE user_id=1").fetchone()[0], "listener")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.track_likes").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT track_id,rank FROM public.recommendation_items").fetchall(), [("track_a", 1)])

    def check_fixed_snapshot_duration_edit(self, isolation):
        self.conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        self.load_listener()
        # Even a seemingly safe reduction must not rely on a fixed snapshot.
        with self.assertRaisesRegex(psycopg.errors.FeatureNotSupported, "READ COMMITTED"):
            with self.conn.transaction():
                self.conn.execute("UPDATE public.songs SET duration_ms=30000 WHERE track_id='track_a'")
        self.conn.execute("UPDATE public.songs SET duration_ms=60000 WHERE track_id='track_a'")
        self.conn.execute("UPDATE public.songs SET duration_ms=90000 WHERE track_id='track_a'")
        self.listen(listened=90000)

    def test_repeatable_read_rejects_duration_decrease(self):
        self.check_fixed_snapshot_duration_edit("REPEATABLE READ")

    def test_serializable_rejects_duration_decrease(self):
        self.check_fixed_snapshot_duration_edit("SERIALIZABLE")

    def test_invalid_listening_references_are_rejected(self):
        self.load_listener()
        for track, user in [("missing_track", 1), ("track_a", 999)]:
            with self.subTest(track=track, user=user), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with self.conn.transaction():
                    self.listen(track, user=user)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.history").fetchone()[0], 0)

    def test_listening_failure_rolls_back_earlier_batch_writes(self):
        self.load_listener()
        existing = self.listen()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO public.users VALUES (2,'batch listener')")
                self.listen("track_b", 120000, user=2)
                self.conn.execute("UPDATE public.songs SET duration_ms=40000 WHERE track_id='track_a'")
                self.listen(listened=50000, user=2)
        self.assertEqual(self.conn.execute("SELECT history_id,listened_ms FROM public.history").fetchall(), [(existing, 30000)])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM public.users WHERE user_id=2").fetchone())
        self.assertEqual(self.conn.execute("SELECT duration_ms FROM public.songs WHERE track_id='track_a'").fetchone()[0], 60000)

    def test_catalog_rerun_preserves_rows_without_duplicates(self):
        from setup_database import import_catalog
        first = self.load_catalog()
        self.assertEqual(first, {"artists": 2, "genres": 2, "albums": 2, "songs": 4, "song_genres": 5})
        self.assertTrue(all(count == 0 for count in import_catalog(self.conn, self.data_dir).values()))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.songs").fetchone()[0], 4)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.song_genres").fetchone()[0], 5)

    def test_catalog_conflict_preserves_existing_values(self):
        from setup_database import import_catalog
        self.load_catalog()
        artists = Path(self.data_dir, "artists.csv")
        artists.write_text(artists.read_text() + "3,Must Not Import\n")
        songs = Path(self.data_dir, "songs.csv")
        songs.write_text(songs.read_text().replace("Song A", "Changed Title"))
        with self.assertRaises(ValueError):
            import_catalog(self.conn, self.data_dir)
        self.assertEqual(self.conn.execute("SELECT track_name FROM public.songs WHERE track_id='track_a'").fetchone()[0], "Song A")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM public.artists WHERE artist_id=3").fetchone())

    def test_foreign_key_failure_rolls_back_earlier_table_inserts(self):
        from setup_database import import_catalog, initialize_schema
        initialize_schema(self.conn)
        albums = Path(self.data_dir, "albums.csv")
        albums.write_text(albums.read_text() + "invalid_album,Missing Artist,2020,999\n")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            import_catalog(self.conn, self.data_dir)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.artists").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.genres").fetchone()[0], 0)

    def test_bad_catalog_files_fail_without_partial_import(self):
        from setup_database import import_catalog, initialize_schema
        initialize_schema(self.conn)
        songs = Path(self.data_dir, "songs.csv")
        valid = songs.read_text()
        with self.subTest("wrong header"):
            songs.write_text(valid.replace("duration_ms", "wrong_column", 1))
            with self.assertRaises(ValueError):
                import_catalog(self.conn, self.data_dir)
        with self.subTest("invalid duration"):
            # Change the normalized field directly to exercise DB validation.
            songs.write_text(valid)
            normalized = pd.read_csv(songs)
            normalized.loc[0, "duration_ms"] = 0
            normalized.to_csv(songs, index=False)
            with self.assertRaises(psycopg.errors.CheckViolation):
                import_catalog(self.conn, self.data_dir)
        with self.subTest("missing source"):
            songs.unlink()
            with self.assertRaises(FileNotFoundError):
                import_catalog(self.conn, self.data_dir)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.artists").fetchone()[0], 0)

    def test_missing_primary_genre_pair_rejects_incomplete_bridge(self):
        from setup_database import import_catalog, initialize_schema
        initialize_schema(self.conn)
        bridge = Path(self.data_dir, "song_genres.csv")
        pairs = pd.read_csv(bridge)
        pairs = pairs[~((pairs["track_id"] == "track_a") & (pairs["genre_id"] == 1))]
        pairs.to_csv(bridge, index=False)
        with self.assertRaisesRegex(ValueError, "Incomplete song_genres"):
            import_catalog(self.conn, self.data_dir)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.artists").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.songs").fetchone()[0], 0)

    def test_full_setup_failure_rolls_back_schema_catalog_and_seed(self):
        from seed_data import seed
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            with self.conn.transaction():
                self.load_catalog()
                self.assertEqual(seed(self.conn)["users"], 100)
                raise RuntimeError("injected failure")
        self.assertIsNone(self.conn.execute("SELECT to_regclass('public.songs')").fetchone()[0])
        self.assertIsNone(self.conn.execute("SELECT to_regclass('public.users')").fetchone()[0])

    def test_seed_rerun_preserves_unlikes_and_manual_timestamps(self):
        from seed_data import seed
        self.load_catalog()
        first = seed(self.conn)
        self.assertEqual(first["users"], 100)
        self.assertGreater(first["history"], 0)
        likes = self.conn.execute("SELECT user_id,track_id FROM public.track_likes ORDER BY user_id,track_id LIMIT 2").fetchall()
        self.assertEqual(len(likes), 2)
        self.conn.execute("DELETE FROM public.track_likes WHERE user_id=%s AND track_id=%s", likes[0])
        manual_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
        self.conn.execute("UPDATE public.track_likes SET liked_when=%s WHERE user_id=%s AND track_id=%s", (manual_time, *likes[1]))
        self.assertEqual(seed(self.conn), {"users": 0, "history": 0, "track_likes": 0})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM public.track_likes WHERE user_id=%s AND track_id=%s", likes[0]).fetchone())
        self.assertEqual(self.conn.execute("SELECT liked_when FROM public.track_likes WHERE user_id=%s AND track_id=%s", likes[1]).fetchone()[0], manual_time)

    def test_conflicting_synthetic_dataset_is_rejected(self):
        from seed_data import FIRST_ID, seed
        self.load_catalog()
        seed(self.conn)
        before = self.conn.execute("SELECT count(*) FROM public.history").fetchone()[0]
        self.conn.execute("UPDATE public.users SET username='conflicting_name' WHERE user_id=%s", (FIRST_ID,))
        with self.assertRaises(ValueError):
            seed(self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM public.history").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
