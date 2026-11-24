-- Application DDL for PostgreSQL 16+. Safe to reapply; this is not a migration
-- that rewrites existing tables. setup_database.py validates compatibility.
CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE IF NOT EXISTS public.artists (
    artist_id integer PRIMARY KEY,
    artist_name text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.genres (
    genre_id integer PRIMARY KEY,
    genre_name varchar(100) NOT NULL
);
CREATE TABLE IF NOT EXISTS public.albums (
    album_id varchar(22) PRIMARY KEY,
    album_name text NOT NULL,
    release_date varchar(10) NOT NULL,
    artist_id integer NOT NULL REFERENCES public.artists(artist_id),
    CONSTRAINT valid_release_date_format CHECK (release_date ~ '^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$')
);
CREATE TABLE IF NOT EXISTS public.songs (
    track_id varchar(300) PRIMARY KEY,
    track_name varchar(300) NOT NULL,
    artist_id integer NOT NULL REFERENCES public.artists(artist_id),
    track_popularity integer NOT NULL CHECK (track_popularity BETWEEN 0 AND 100),
    album_id varchar(300) NOT NULL REFERENCES public.albums(album_id),
    genre_id integer NOT NULL REFERENCES public.genres(genre_id),
    subgenre text NOT NULL,
    duration_ms integer NOT NULL CHECK (duration_ms > 0)
);
CREATE TABLE IF NOT EXISTS public.song_genres (
    track_id varchar NOT NULL REFERENCES public.songs(track_id),
    genre_id integer NOT NULL REFERENCES public.genres(genre_id),
    PRIMARY KEY (track_id, genre_id)
);
CREATE TABLE IF NOT EXISTS public.users (
    user_id integer PRIMARY KEY,
    username varchar(100) NOT NULL
);
CREATE TABLE IF NOT EXISTS public.history (
    user_id integer NOT NULL REFERENCES public.users(user_id),
    track_id varchar(50) NOT NULL REFERENCES public.songs(track_id),
    played_when timestamptz NOT NULL,
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listened_ms integer NOT NULL CHECK (listened_ms >= 0)
);
CREATE TABLE IF NOT EXISTS public.track_likes (
    user_id integer NOT NULL REFERENCES public.users(user_id),
    track_id varchar(50) NOT NULL REFERENCES public.songs(track_id),
    liked_when timestamptz NOT NULL,
    PRIMARY KEY (user_id, track_id)
);
CREATE TABLE IF NOT EXISTS public.generation_batches (
    batch_id text PRIMARY KEY,
    generated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    likes_count integer NOT NULL CHECK (likes_count >= 0)
);
CREATE TABLE IF NOT EXISTS public.recommendation_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id integer NOT NULL REFERENCES public.users(user_id),
    method text NOT NULL CHECK (method IN ('popularity_v1', 'genre_v1', 'genre_v2')),
    generated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    history_cutoff timestamptz NOT NULL,
    requested_k integer NOT NULL CHECK (requested_k > 0)
);
CREATE TABLE IF NOT EXISTS public.recommendation_items (
    run_id bigint NOT NULL REFERENCES public.recommendation_runs(run_id) ON DELETE CASCADE,
    track_id varchar(300) NOT NULL REFERENCES public.songs(track_id),
    rank integer NOT NULL CHECK (rank > 0),
    score numeric NOT NULL CHECK (score >= 0),
    PRIMARY KEY (run_id, track_id),
    UNIQUE (run_id, rank)
);

-- Optional provenance/staging relation retained from the original schema.
-- The reproducible importer reads checked-in cleaned CSVs, not this table.
CREATE TABLE IF NOT EXISTS public.spotify_raw (
    track_id text, track_name text, track_artist text, track_popularity text,
    track_album_id text, track_album_name text, track_album_release_date text,
    playlist_name text, playlist_id text, playlist_genre text, playlist_subgenre text,
    danceability text, energy text, key text, loudness text, mode text,
    speechiness text, acousticness text, instrumentalness text, liveness text,
    valence text, tempo text, duration_ms text, release_date_precision text
);

CREATE OR REPLACE FUNCTION public.check_listen_duration() RETURNS trigger
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    song_duration integer;
BEGIN
    -- Lock the referenced row while validating the cross-table invariant.
    SELECT duration_ms INTO song_duration FROM public.songs
    WHERE track_id = NEW.track_id FOR SHARE;
    IF NEW.listened_ms > song_duration THEN
        RAISE EXCEPTION 'Listening time exceeds song duration' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER check_listen_duration
BEFORE INSERT OR UPDATE OF track_id, listened_ms ON public.history
FOR EACH ROW EXECUTE FUNCTION public.check_listen_duration();

CREATE OR REPLACE FUNCTION public.check_song_duration() RETURNS trigger
LANGUAGE plpgsql VOLATILE AS $$
BEGIN
    -- A fixed transaction snapshot could miss a recently committed listen.
    IF current_setting('transaction_isolation') NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'Song-duration decreases require READ COMMITTED isolation'
            USING ERRCODE = '0A000', HINT = 'Retry the duration update in a READ COMMITTED transaction.';
    END IF;
    -- UPDATE already locks this song, conflicting with history's FOR SHARE.
    IF EXISTS (SELECT 1 FROM public.history
               WHERE track_id = OLD.track_id AND listened_ms > NEW.duration_ms) THEN
        RAISE EXCEPTION 'Song duration cannot be shorter than an existing listen'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER check_song_duration
BEFORE UPDATE OF duration_ms ON public.songs
FOR EACH ROW WHEN (NEW.duration_ms < OLD.duration_ms)
EXECUTE FUNCTION public.check_song_duration();
