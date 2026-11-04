--
-- PostgreSQL database dump
--

\restrict zu9Lq5wPkHdYy8YUJwcfrw5BuQqGsYszqqURDQzkLIk1UFUPpeaww8rqxglsuv2

-- Dumped from database version 18.6 (ac70818)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: check_listen_duration(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.check_listen_duration() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    song_duration integer;
BEGIN
    SELECT duration_ms INTO song_duration
    FROM public.songs
    WHERE track_id = NEW.track_id
    FOR SHARE;

    IF NEW.listened_ms > song_duration THEN
        RAISE EXCEPTION 'Listening time exceeds song duration'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: albums; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.albums (
    album_id character varying(22) NOT NULL,
    album_name text NOT NULL,
    release_date character varying(10) NOT NULL,
    artist_id integer NOT NULL,
    CONSTRAINT valid_release_date_format CHECK (((release_date)::text ~ '^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$'::text))
);


--
-- Name: artists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artists (
    artist_id integer NOT NULL,
    artist_name text NOT NULL
);


--
-- Name: genres; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.genres (
    genre_id integer NOT NULL,
    genre_name character varying(100) NOT NULL
);


--
-- Name: history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.history (
    user_id integer NOT NULL,
    track_id character varying(50) NOT NULL,
    played_when timestamp with time zone NOT NULL,
    history_id bigint NOT NULL,
    listened_ms integer NOT NULL,
    CONSTRAINT history_listened_ms_check CHECK ((listened_ms >= 0))
);


--
-- Name: history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.history ALTER COLUMN history_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: song_genres; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.song_genres (
    track_id character varying NOT NULL,
    genre_id integer NOT NULL
);


--
-- Name: songs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.songs (
    track_id character varying(300) NOT NULL,
    track_name character varying(300) NOT NULL,
    artist_id integer NOT NULL,
    track_popularity integer NOT NULL,
    album_id character varying(300) NOT NULL,
    genre_id integer NOT NULL,
    subgenre text NOT NULL,
    duration_ms integer NOT NULL,
    CONSTRAINT songs_duration_ms_check CHECK ((duration_ms > 0)),
    CONSTRAINT songs_track_popularity_check CHECK (((track_popularity >= 0) AND (track_popularity <= 100)))
);


--
-- Name: spotify_raw; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.spotify_raw (
    track_id text,
    track_name text,
    track_artist text,
    track_popularity text,
    track_album_id text,
    track_album_name text,
    track_album_release_date text,
    playlist_name text,
    playlist_id text,
    playlist_genre text,
    playlist_subgenre text,
    danceability text,
    energy text,
    key text,
    loudness text,
    mode text,
    speechiness text,
    acousticness text,
    instrumentalness text,
    liveness text,
    valence text,
    tempo text,
    duration_ms text,
    release_date_precision text
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id integer NOT NULL,
    username character varying(100) NOT NULL
);


--
-- Name: albums albums_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_pkey PRIMARY KEY (album_id);


--
-- Name: artists artists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artists
    ADD CONSTRAINT artists_pkey PRIMARY KEY (artist_id);


--
-- Name: genres genres_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.genres
    ADD CONSTRAINT genres_pkey PRIMARY KEY (genre_id);


--
-- Name: history history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.history
    ADD CONSTRAINT history_pkey PRIMARY KEY (history_id);


--
-- Name: song_genres song_genres_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.song_genres
    ADD CONSTRAINT song_genres_pkey PRIMARY KEY (track_id, genre_id);


--
-- Name: songs songs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.songs
    ADD CONSTRAINT songs_pkey PRIMARY KEY (track_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: history check_listen_duration; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER check_listen_duration BEFORE INSERT OR UPDATE OF track_id, listened_ms ON public.history FOR EACH ROW EXECUTE FUNCTION public.check_listen_duration();


--
-- Name: albums albums_artist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.albums
    ADD CONSTRAINT albums_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.artists(artist_id);


--
-- Name: history history_track_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.history
    ADD CONSTRAINT history_track_id_fkey FOREIGN KEY (track_id) REFERENCES public.songs(track_id);


--
-- Name: history history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.history
    ADD CONSTRAINT history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: song_genres song_genres_genre_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.song_genres
    ADD CONSTRAINT song_genres_genre_id_fkey FOREIGN KEY (genre_id) REFERENCES public.genres(genre_id);


--
-- Name: song_genres song_genres_track_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.song_genres
    ADD CONSTRAINT song_genres_track_id_fkey FOREIGN KEY (track_id) REFERENCES public.songs(track_id);


--
-- Name: songs songs_album_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.songs
    ADD CONSTRAINT songs_album_id_fkey FOREIGN KEY (album_id) REFERENCES public.albums(album_id);


--
-- Name: songs songs_artist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.songs
    ADD CONSTRAINT songs_artist_id_fkey FOREIGN KEY (artist_id) REFERENCES public.artists(artist_id);


--
-- Name: songs songs_genre_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.songs
    ADD CONSTRAINT songs_genre_id_fkey FOREIGN KEY (genre_id) REFERENCES public.genres(genre_id);


--
-- PostgreSQL database dump complete
--

\unrestrict zu9Lq5wPkHdYy8YUJwcfrw5BuQqGsYszqqURDQzkLIk1UFUPpeaww8rqxglsuv2

