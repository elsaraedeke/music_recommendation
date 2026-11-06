from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st

from db import get_connection

METHODS = {"genre": "Genre preferences", "popularity": "Popularity"}
LISTENS = """WITH listens AS (
    SELECT * FROM public.history WHERE user_id = %(user_id)s
    AND played_when >= %(start)s AND played_when < %(end)s
) """


def listening_data(user_id, start, end):
    params = {"user_id": user_id, "start": start, "end": end}
    with get_connection(read_only=True) as conn:
        def frame(query, columns):
            return pd.DataFrame(conn.execute(LISTENS + query, params).fetchall(), columns=columns)

        totals = conn.execute(LISTENS + """
            SELECT COALESCE(SUM(h.listened_ms), 0) / 60000.0, COUNT(*),
                   COUNT(DISTINCT h.track_id), COUNT(DISTINCT s.artist_id)
            FROM listens h JOIN public.songs s ON s.track_id = h.track_id""", params).fetchone()
        monthly = frame("""
            SELECT to_char(played_when AT TIME ZONE 'UTC', 'YYYY-MM'), SUM(listened_ms) / 60000.0
            FROM listens GROUP BY 1 ORDER BY 1""", ["Month", "Minutes"])
        songs = frame("""
            SELECT s.track_name, a.artist_name, COUNT(*) AS plays, SUM(h.listened_ms) / 60000.0 AS minutes
            FROM listens h JOIN public.songs s ON s.track_id = h.track_id
            JOIN public.artists a ON a.artist_id = s.artist_id
            GROUP BY s.track_id, a.artist_id ORDER BY plays DESC, minutes DESC, s.track_id LIMIT 10
            """, ["Song", "Artist", "Plays", "Minutes"])
        artists = frame("""
            SELECT a.artist_name, COUNT(*) AS plays, SUM(h.listened_ms) / 60000.0 AS minutes
            FROM listens h JOIN public.songs s ON s.track_id = h.track_id
            JOIN public.artists a ON a.artist_id = s.artist_id
            GROUP BY a.artist_id ORDER BY plays DESC, minutes DESC, a.artist_id LIMIT 10
            """, ["Artist", "Plays", "Minutes"])
        genres = frame(""", genre_counts AS (
                SELECT track_id, COUNT(*) AS n FROM public.song_genres GROUP BY track_id
            )
            SELECT COALESCE(g.genre_name, 'Uncategorized'),
                   SUM(h.listened_ms::numeric / COALESCE(gc.n, 1)) / 60000.0 AS minutes
            FROM listens h LEFT JOIN public.song_genres sg ON sg.track_id = h.track_id
            LEFT JOIN genre_counts gc ON gc.track_id = h.track_id
            LEFT JOIN public.genres g ON g.genre_id = sg.genre_id
            GROUP BY 1 ORDER BY minutes DESC, 1""", ["Genre", "Minutes"])
        history = frame("""
            SELECT h.played_when AT TIME ZONE 'UTC', s.track_name, a.artist_name,
                   h.listened_ms / 60000.0, ROUND(100.0 * h.listened_ms / s.duration_ms, 1)
            FROM listens h JOIN public.songs s ON s.track_id = h.track_id
            JOIN public.artists a ON a.artist_id = s.artist_id
            ORDER BY h.played_when DESC, h.history_id DESC
            """, ["Played at (UTC)", "Song", "Artist", "Minutes", "Completion (%)"])
    for data in (monthly, songs, artists, genres, history):
        data["Minutes"] = data["Minutes"].astype(float)
    history["Completion (%)"] = history["Completion (%)"].astype(float)
    months = pd.period_range(start.date(), (end - timedelta(days=1)).date(), freq="M").astype(str)
    monthly = monthly.set_index("Month").reindex(months, fill_value=0)
    monthly.index.name = "Month"
    return totals, monthly, songs, artists, genres, history


def recommendations(user_id, cutoff):
    st.caption("Use all listening through the selected end date. The start date only filters statistics and history.")
    with st.form("recommendations"):
        left, right = st.columns([2, 1])
        method = left.selectbox("Recommend by", list(METHODS), format_func=METHODS.get)
        k = right.number_input("Number of songs", min_value=1, max_value=50, value=10)
        generate = st.form_submit_button("Generate & save", type="primary")
    if generate:
        query = (Path(__file__).parent / "queries" / f"{method}_candidates.sql").read_text()
        with st.spinner("Finding your next songs…"):
            with get_connection() as conn:
                rows = conn.execute(query, {"user_id": user_id, "cutoff": cutoff, "k": k}).fetchall()
                run_id = conn.execute(
                    """INSERT INTO public.recommendation_runs
                       (user_id, method, history_cutoff, requested_k)
                       VALUES (%s, %s, %s, %s) RETURNING run_id""",
                    (user_id, f"{method}_v1", cutoff, k),
                ).fetchone()[0]
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO public.recommendation_items (run_id, track_id, rank, score) VALUES (%s, %s, %s, %s)",
                        [(run_id, row[0], rank, row[3]) for rank, row in enumerate(rows, 1)],
                    )
        st.session_state[f"saved_run_{user_id}"] = run_id
        st.success(f"Saved {len(rows)} recommendations.")

    with get_connection(read_only=True) as conn:
        runs = conn.execute(
            """SELECT run_id, method, generated_at, history_cutoff FROM public.recommendation_runs
               WHERE user_id = %s ORDER BY generated_at DESC, run_id DESC""",
            (user_id,),
        ).fetchall()
        if not runs:
            st.info("Choose a method and generate your first list.")
            return
        runs = {row[0]: row[1:] for row in runs}
        run_id = st.selectbox(
            "Saved lists", list(runs), key=f"saved_run_{user_id}",
            format_func=lambda rid: f"{runs[rid][1].astimezone(timezone.utc):%b %d, %Y %H:%M:%S} · {METHODS[runs[rid][0].removesuffix('_v1')]}",
        )
        saved = conn.execute(
            """SELECT i.rank, s.track_name, a.artist_name, i.score
               FROM public.recommendation_items i
               JOIN public.songs s ON s.track_id = i.track_id
               JOIN public.artists a ON a.artist_id = s.artist_id
               WHERE i.run_id = %s ORDER BY i.rank""",
            (run_id,),
        ).fetchall()
    st.caption(f"This list uses listening before {runs[run_id][2].astimezone(timezone.utc):%b %d, %Y %H:%M UTC}.")
    if not saved:
        st.info("No eligible songs were found for this listener.")
        return
    results = pd.DataFrame(saved, columns=["Rank", "Song", "Artist", "Score"])
    results["Score"] = results["Score"].astype(float)
    st.dataframe(results, hide_index=True, width="stretch")
    st.bar_chart(results.set_index("Rank")["Score"])
    st.caption("Scores rank songs within each method. Genre preferences use popularity when listening history is unavailable.")


st.set_page_config(page_title="Music & Listening History", page_icon="🎵", layout="wide")
st.title("Your music dashboard")
st.caption("Explore your listening habits and discover your next favorite song.")

try:
    with get_connection(read_only=True) as conn:
        users = dict(conn.execute("SELECT user_id, username FROM public.users ORDER BY username"))
        first, last = conn.execute("""SELECT MIN((played_when AT TIME ZONE 'UTC')::date),
            MAX((played_when AT TIME ZONE 'UTC')::date) FROM public.history""").fetchone()
    if not users:
        st.info("Add users to the database to get started.")
        st.stop()
    now = datetime.now(timezone.utc)
    st.sidebar.header("Your listening")
    user_id = st.sidebar.selectbox("Listener", list(users), format_func=lambda uid: f"{users[uid]} ({uid})")
    start_date = st.sidebar.date_input("From", min(first or now.date(), now.date()), max_value=now.date())
    end_date = st.sidebar.date_input("Through", min(last or now.date(), now.date()), max_value=now.date())
    st.sidebar.caption("Dates include the full selected days. All times are UTC.")
    if not start_date or not end_date or start_date > end_date:
        st.error("Choose a start date on or before the end date.")
        st.stop()
    start = datetime.combine(start_date, time.min, timezone.utc)
    end = datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc)
    totals, monthly, songs, artists, genres, history = listening_data(user_id, start, end)
    overview, top_music, listening, recs = st.tabs(["Overview", "Top Music", "Listening History", "Recommendations"])

    with overview:
        for column, label, value in zip(st.columns(4),
            ["Minutes listened", "Listening events", "Unique songs", "Unique artists"],
            [f"{totals[0]:,.1f}", *totals[1:]]):
            column.metric(label, value)
        st.subheader("Listening by month")
        if history.empty:
            st.info("No listening history in this date range.")
        else:
            st.bar_chart(monthly["Minutes"])

    with top_music:
        if history.empty:
            st.info("Listen to some music to see your favorites here.")
        else:
            left, right = st.columns(2)
            left.subheader("Top songs")
            left.dataframe(songs.round(2), hide_index=True, width="stretch")
            right.subheader("Top artists")
            right.dataframe(artists.round(2), hide_index=True, width="stretch")
            st.subheader("Listening by genre")
            st.bar_chart(genres.set_index("Genre")["Minutes"])
            st.caption("Listening time is split equally across a song's genres, so it is counted only once in total.")

    with listening:
        search = st.text_input("Search songs or artists", placeholder="Type a song or artist name")
        shown = history
        if search and not history.empty:
            shown = history[(history["Song"] + " " + history["Artist"]).str.contains(search, case=False, regex=False)]
        st.caption(f"{len(shown):,} listening events")
        if shown.empty:
            st.info("No listening events match these filters.")
        else:
            st.dataframe(shown.round(2), hide_index=True, width="stretch")

    with recs:
        recommendations(user_id, min(end, now))
except (psycopg.Error, ValueError, OSError):
    st.error("Could not load or save data. Check your database connection, permissions, and query files.")
