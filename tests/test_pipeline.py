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
