from datetime import datetime, time, timedelta, timezone
from html import escape
import logging
import re

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from db import get_connection
from queries.recommend import METHOD_VERSIONS, save_recommendations

METHODS = {"genre": "Genre preferences", "popularity": "Popularity"}
SAVED_METHODS = {version: METHODS[method] for method, version in METHOD_VERSIONS.items()}
SAVED_METHODS["genre_v1"] = "Genre preferences (earlier version)"
LISTENS = """WITH listens AS (
    SELECT * FROM public.history WHERE user_id = %(user_id)s
    AND played_when >= %(start)s AND played_when < %(end)s
) """
ACCENT = "#2563eb"
logger = logging.getLogger(__name__)
PAGES = ["Listening", "Discover", "Likes"]


@st.cache_data(ttl=60, max_entries=32, show_spinner=False)
def listening_data(user_id, start, end, section="Overview"):
    params = {"user_id": user_id, "start": start, "end": end}
    with get_connection(read_only=True) as conn:
        def frame(query, columns):
            return pd.DataFrame(conn.execute(LISTENS + query, params).fetchall(), columns=columns)

        if section == "History":
            history = frame("""
                SELECT h.history_id, s.track_id, h.played_when AT TIME ZONE 'UTC', s.track_name, a.artist_name,
                       h.listened_ms / 60000.0, ROUND(100.0 * h.listened_ms / s.duration_ms, 1)
                FROM listens h JOIN public.songs s ON s.track_id = h.track_id
                JOIN public.artists a ON a.artist_id = s.artist_id
                ORDER BY h.played_when DESC, h.history_id DESC
                """, ["Event ID", "Track ID", "Played at (UTC)", "Song", "Artist", "Minutes", "Completion (%)"])
            return history.astype({"Minutes": float, "Completion (%)": float})
        if section == "Top Music":
            songs = frame("""
                SELECT s.track_id, s.track_name, a.artist_name, COUNT(*) AS plays,
                       SUM(h.listened_ms) / 60000.0 AS minutes
                FROM listens h JOIN public.songs s ON s.track_id = h.track_id
                JOIN public.artists a ON a.artist_id = s.artist_id
                GROUP BY s.track_id, a.artist_id ORDER BY plays DESC, minutes DESC, s.track_id
                """, ["Track ID", "Song", "Artist", "Plays", "Minutes"])
            artists = frame("""
                SELECT a.artist_id, a.artist_name, COUNT(*) AS plays, SUM(h.listened_ms) / 60000.0 AS minutes
                FROM listens h JOIN public.songs s ON s.track_id = h.track_id
                JOIN public.artists a ON a.artist_id = s.artist_id
                GROUP BY a.artist_id ORDER BY plays DESC, minutes DESC, a.artist_id
                """, ["Artist ID", "Artist", "Plays", "Minutes"])
            return songs.astype({"Minutes": float}), artists.astype({"Minutes": float})
        totals = conn.execute(LISTENS + """
            SELECT COALESCE(SUM(h.listened_ms), 0) / 60000.0, COUNT(*),
                   COUNT(DISTINCT h.track_id), COUNT(DISTINCT s.artist_id)
            FROM listens h JOIN public.songs s ON s.track_id = h.track_id""", params).fetchone()
        daily = frame("""
            SELECT (played_when AT TIME ZONE 'UTC')::date, SUM(listened_ms) / 60000.0, COUNT(*)
            FROM listens GROUP BY 1 ORDER BY 1""", ["Date", "Minutes", "Events"])
        genres = frame(""", genre_counts AS (
                SELECT track_id, COUNT(*) AS n FROM public.song_genres GROUP BY track_id
            )
            SELECT COALESCE(g.genre_name, 'Uncategorized'),
                   SUM(h.listened_ms::numeric / COALESCE(gc.n, 1)) / 60000.0 AS minutes
            FROM listens h LEFT JOIN public.song_genres sg ON sg.track_id = h.track_id
            LEFT JOIN genre_counts gc ON gc.track_id = h.track_id
            LEFT JOIN public.genres g ON g.genre_id = sg.genre_id
            GROUP BY 1 ORDER BY minutes DESC, 1""", ["Genre", "Minutes"])
    for data in (daily, genres):
        data["Minutes"] = data["Minutes"].astype(float)
    daily["Date"] = pd.to_datetime(daily["Date"])
    days = pd.date_range(start.date(), (end - timedelta(days=1)).date(), name="Date")
    daily = daily.set_index("Date").reindex(days, fill_value=0)
    return totals, daily, genres


def liked_songs(user_id):
    with get_connection(read_only=True) as conn:
        rows = conn.execute("""SELECT l.track_id, s.track_name, a.artist_name,
            l.liked_when AT TIME ZONE 'UTC' FROM public.track_likes l
            JOIN public.songs s ON s.track_id = l.track_id
            JOIN public.artists a ON a.artist_id = s.artist_id
            WHERE l.user_id = %s ORDER BY l.liked_when DESC, l.track_id""", (user_id,)).fetchall()
    return pd.DataFrame(rows, columns=["Track ID", "Song", "Artist", "Liked at (UTC)"])


def set_like(user_id, track_id, liked, liked_when=None):
    with get_connection() as conn:
        if liked:
            conn.execute("""INSERT INTO public.track_likes (user_id, track_id, liked_when)
                VALUES (%s, %s, COALESCE(%s, CURRENT_TIMESTAMP)) ON CONFLICT (user_id, track_id) DO NOTHING""",
                (user_id, track_id, liked_when))
        else:
            removed = conn.execute("DELETE FROM public.track_likes WHERE user_id = %s AND track_id = %s RETURNING liked_when",
                                   (user_id, track_id)).fetchone()
            return removed[0] if removed else None


def change_like(user_id, track_id, liked, song, artist):
    try:
        timestamp = set_like(user_id, track_id, liked)
    except (psycopg.Error, ValueError, OSError):
        logger.exception("Could not change a like")
        st.session_state[f"notice_{user_id}"] = ("error", "Couldn't confirm your change. Refresh to check your likes, then try again.")
        return
    st.session_state[f"notice_{user_id}"] = ("success", f"{'Saved' if liked else 'Removed'} {song} {'to' if liked else 'from'} your likes.")
    st.session_state.pop(f"undo_{user_id}", None)
    if not liked and timestamp is not None:
        st.session_state[f"undo_{user_id}"] = (track_id, song, artist, timestamp, datetime.now(timezone.utc))


def undo_like(user_id):
    removed = st.session_state.get(f"undo_{user_id}")
    if not removed or datetime.now(timezone.utc) - removed[4] > timedelta(minutes=2):
        st.session_state.pop(f"undo_{user_id}", None)
        return
    try:
        set_like(user_id, removed[0], True, removed[3])
    except (psycopg.Error, ValueError, OSError):
        logger.exception("Could not undo a removed like")
        st.session_state[f"notice_{user_id}"] = ("error", "Couldn't restore the song. Try Undo again.")
    else:
        st.session_state.pop(f"undo_{user_id}", None)
        st.session_state[f"notice_{user_id}"] = ("success", f"Restored {removed[1]} to your likes.")


def like_feedback(user_id):
    with st.container(key=f"like_feedback_{user_id}"):
        notice = st.session_state.pop(f"notice_{user_id}", None)
        if notice:
            getattr(st, notice[0])(notice[1])
        removed = st.session_state.get(f"undo_{user_id}")
        if removed and datetime.now(timezone.utc) - removed[4] <= timedelta(minutes=2):
            st.button(f"Undo removal of {removed[1]}", key=f"undo_button_{user_id}", on_click=undo_like, args=(user_id,))
        elif removed:
            del st.session_state[f"undo_{user_id}"]


def change_page(key, page):
    st.session_state[key] = page


def track_list(data, user_id, liked_ids, section, scope=None):
    if data.empty:
        st.info("No songs match these filters.")
        return
    page_key = f"page_{section}_{user_id}"
    scope_key = f"scope_{section}_{user_id}"
    pages = (len(data) + 9) // 10
    if st.session_state.get(scope_key) != scope:
        st.session_state[page_key] = 1
        st.session_state[scope_key] = scope
    st.session_state[page_key] = min(max(int(st.session_state.get(page_key, 1)), 1), pages)
    page = st.session_state[page_key]
    offset = (page - 1) * 10
    visible = data.iloc[offset:offset + 10]
    liked_buttons = []
    for _, row in visible.iterrows():
        if row["Track ID"] in liked_ids:
            key = f"heart_{section}_{user_id}_{row.get('Event ID', row['Track ID'])}"
            liked_buttons.append(".st-key-" + re.sub(r"[^a-zA-Z0-9_-]", "-", key) + " button")
    style = ",".join(liked_buttons) + "{--heart-icon:var(--heart-filled);}" if liked_buttons else ""
    st.html(f"<style>{style}</style>")
    def pagination(position):
        if pages > 1:
            previous, current, following = st.columns(3)
            previous.button("Previous", key=f"{page_key}_{position}_prev", disabled=page == 1,
                            on_click=change_page, args=(page_key, page - 1))
            current.write(f"Page {page} of {pages}")
            following.button("Next", key=f"{page_key}_{position}_next", disabled=page == pages,
                             on_click=change_page, args=(page_key, page + 1))
    if pages > 1:
        st.caption(f"{offset + 1}–{min(offset + 10, len(data))} of {len(data):,} songs")
    for _, row in visible.iterrows():
        track_id = row["Track ID"]
        occurrence = row.get("Event ID", track_id)
        liked = track_id in liked_ids
        with st.container(key=f"song_row_{section}_{user_id}_{occurrence}"):
            title, heart = st.columns([9, 1], vertical_alignment="center")
            with title:
                st.text(row["Song"])
            details = [row["Artist"]]
            if "Reason" in row:
                details.append(row["Reason"])
            if "Plays" in row:
                plays = int(row["Plays"])
                details.append(f"{plays} {'play' if plays == 1 else 'plays'}")
            if "Minutes" in row:
                minutes, seconds = divmod(round(row["Minutes"] * 60), 60)
                details.append(f"{minutes}m {seconds:02}s")
            if "Completion (%)" in row:
                details.append(f"{row['Completion (%)']:.0f}% completed")
            if "Played at (UTC)" in row:
                details.append(f"{row['Played at (UTC)']:%b %d, %Y · %H:%M} UTC")
            title.caption(" · ".join(details))
            action = f"{'Unlike' if liked else 'Like'} {row['Song']} by {row['Artist']}"
            heart.button(action, type="tertiary", help=action,
                key=f"heart_{section}_{user_id}_{occurrence}", on_click=change_like,
                args=(user_id, track_id, not liked, row["Song"], row["Artist"]))
    pagination("bottom")


def activity_calendar(daily):
    """Render the selected days as fixed-size Sunday-to-Saturday contribution cells."""
    if daily.empty:
        return "<p>No dates selected.</p>"
    first, last = daily.index.min(), daily.index.max()
    grid_start = pd.Timestamp(first.to_pydatetime() - timedelta(days=(first.dayofweek + 1) % 7))
    weeks = (last - grid_start).days // 7 + 1
    pieces = []
    month_positions = {}
    for day in daily.index:
        if day == first or day.day == 1:
            week = (day - grid_start).days // 7
            if month_positions and week - next(reversed(month_positions)) < 3:
                month_positions.popitem()
            month_positions[week] = day.strftime("%b")
    for week, month in month_positions.items():
        pieces.append(f'<span class="activity-month" style="grid-column:{week + 2};grid-row:1">{month}</span>')
    for weekday, name in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        pieces.append(f'<span class="activity-weekday" style="grid-column:1;grid-row:{weekday + 2}">{name}</span>')
    for offset, day in enumerate(pd.date_range(grid_start, periods=weeks * 7)):
        position = f"grid-column:{offset // 7 + 2};grid-row:{offset % 7 + 2}"
        if day < first or day > last:
            pieces.append(f'<span class="activity-blank" style="{position}" aria-hidden="true"></span>')
            continue
        events, minutes = int(daily.loc[day, "Events"]), float(daily.loc[day, "Minutes"])
        level = 0 if events == 0 else 1 if events == 1 else 2 if events <= 3 else 3 if events <= 6 else 4
        description = escape(f"{day:%B %d, %Y} · {events} listening events · {minutes:.1f} minutes (UTC)", quote=True)
        pieces.append(f'<span class="activity-day activity-level-{level}" style="{position}" '
                      f'title="{description}"></span>')
    legend = "".join(f'<span class="activity-legend-item"><span class="activity-swatch activity-level-{level}" '
                     f'aria-hidden="true"></span>{label}</span>'
                     for level, label in enumerate(["0", "1", "2–3", "4–6", "7+"]))
    events, active_days = int(daily["Events"].sum()), int((daily["Events"] > 0).sum())
    summary = escape(f"Daily listening activity from {first:%B %d, %Y} to {last:%B %d, %Y}: "
                     f"{events:,} listening events across {active_days:,} active days. "
                     "Use Daily activity details below to inspect a date.", quote=True)
    return f"""<style>
        .activity-calendar {{border:1px solid rgba(128,128,128,.22);border-radius:12px;padding:18px;}}
        .activity-scroll {{overflow-x:auto;padding:2px 24px 8px 0;}}
        .activity-grid {{display:grid;grid-template-columns:2.6rem repeat({weeks},13px);
            grid-template-rows:1.4rem repeat(7,13px);gap:3px;width:max-content;}}
        .activity-day,.activity-swatch {{display:inline-block;width:13px;height:13px;box-sizing:border-box;
            border:0;border-radius:2px;}}
        .activity-day:hover {{filter:brightness(1.15);}}
        .activity-level-0 {{background:rgba(128,128,128,.13);}}
        .activity-level-1 {{background:#bfdbfe;}}
        .activity-level-2 {{background:#60a5fa;}}
        .activity-level-3 {{background:#2563eb;}}
        .activity-level-4 {{background:#1e40af;}}
        .activity-month,.activity-weekday {{font-size:.875rem;line-height:1;white-space:nowrap;}}
        .activity-footer {{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
            font-size:.875rem;margin-top:8px;}}
        .activity-legend {{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}}
        .activity-legend-item {{display:inline-flex;align-items:center;gap:4px;}}
        </style><div class="activity-calendar" aria-label="Daily listening activity">
        <div role="img" aria-label="{summary}"><div class="activity-scroll" aria-hidden="true">
        <div class="activity-grid">{''.join(pieces)}</div></div></div>
        <div class="activity-footer"><span>{events:,} listening events · {active_days:,} active days</span>
        <span class="activity-legend">Events/day: {legend}</span></div></div>"""


def ranking_chart(data, label, metric, title):
    return alt.Chart(data).mark_bar(color=ACCENT, cornerRadiusEnd=4).encode(
        x=alt.X(f"{metric}:Q", title=metric, axis=alt.Axis(tickCount=6)),
        y=alt.Y(f"{label}:N", sort="-x", title=None,
                axis=alt.Axis(labelLimit=500, labelOverlap=False, labelFontSize=13, labelPadding=10)),
        tooltip=[label, alt.Tooltip(f"{metric}:Q", format=".2f" if metric == "Minutes" else ",d")],
    ).properties(title=title, height=alt.Step(38))


def recommendations(user_id, cutoff, liked_ids):
    choice_key = f"saved_run_choice_{user_id}"
    st.caption("Find songs you haven't heard, based on your listening.")
    with st.form("recommendations", border=False):
        generate = st.form_submit_button("Find songs", type="primary")
        with st.expander("Options"):
            method = st.selectbox("Recommend by", list(METHODS), format_func=METHODS.get)
            k = st.number_input("Number of songs", min_value=1, max_value=50, value=10)
            st.caption("Genre uses how fully you listen to songs; popularity uses catalog scores. New listeners get popular picks. "
                       "Popular songs fill any remaining spots. Lists save automatically. Hearts save favorites without changing recommendations.")
    if generate:
        try:
            with st.spinner("Finding your next songs…"):
                with get_connection() as conn:
                    run_id = save_recommendations(conn, user_id, method, cutoff, k)
                    count = conn.execute("SELECT COUNT(*) FROM public.recommendation_items WHERE run_id = %s", (run_id,)).fetchone()[0]
            st.session_state[f"saved_run_{user_id}"] = run_id
            st.session_state[choice_key] = run_id
            st.success(f"{count} songs found and saved.")
        except psycopg.errors.CheckViolation as error:
            logger.exception("Could not save recommendations")
            if error.diag.constraint_name == "recommendation_runs_method_check":
                st.error("The database needs the recommendation version update. Ask the database owner to apply it, then try again.")
            else:
                st.error("Couldn't save this list. Please try again.")
        except (psycopg.Error, ValueError, OSError):
            logger.exception("Could not generate recommendations")
            st.error("Couldn't confirm the save. Check Saved lists before trying again.")

    try:
        with get_connection(read_only=True) as conn:
            runs = conn.execute("""SELECT run_id, method, generated_at, history_cutoff, requested_k FROM public.recommendation_runs
                WHERE user_id = %s ORDER BY generated_at DESC, run_id DESC""", (user_id,)).fetchall()
            if not runs:
                st.info("Select Find songs to get your first picks.")
                return
            runs = {row[0]: row[1:] for row in runs}
            labels = {rid: f"List {len(runs) - i} · {row[1].astimezone(timezone.utc):%b %d, %Y} · "
                      f"{SAVED_METHODS.get(row[0], row[0])}"
                      for i, (rid, row) in enumerate(runs.items())}
            key = f"saved_run_{user_id}"
            if st.session_state.get(key) not in runs:
                st.session_state[key] = st.session_state.get(choice_key) if st.session_state.get(choice_key) in runs else next(iter(runs))
            with st.expander("Saved lists"):
                run_id = st.selectbox("Choose a list", list(runs), key=key, format_func=labels.get)
            st.session_state[choice_key] = run_id
            saved_method, generated_at, saved_cutoff, requested_k = runs[run_id]
            saved = conn.execute("""WITH genre_counts AS (
                    SELECT track_id, COUNT(*) AS n FROM public.song_genres GROUP BY track_id
                ), preferences AS (
                    SELECT sg.genre_id,
                           SUM(h.listened_ms::numeric / heard.duration_ms / gc.n) AS listening_time
                    FROM public.history h JOIN public.song_genres sg ON sg.track_id = h.track_id
                    JOIN public.songs heard ON heard.track_id = h.track_id
                    JOIN genre_counts gc ON gc.track_id = h.track_id
                    WHERE h.user_id = %s AND h.played_when < %s AND h.listened_ms > 0
                    GROUP BY sg.genre_id
                )
                SELECT i.track_id, i.rank, s.track_name, a.artist_name, i.score,
                    (SELECT g.genre_name FROM public.song_genres sg
                     JOIN preferences p ON p.genre_id = sg.genre_id
                     JOIN public.genres g ON g.genre_id = sg.genre_id
                     WHERE sg.track_id = i.track_id
                     ORDER BY p.listening_time DESC, g.genre_name LIMIT 1),
                    EXISTS (SELECT 1 FROM preferences)
                FROM public.recommendation_items i JOIN public.songs s ON s.track_id = i.track_id
                JOIN public.artists a ON a.artist_id = s.artist_id
                WHERE i.run_id = %s ORDER BY i.rank""", (user_id, saved_cutoff, run_id)).fetchall()
    except (psycopg.Error, ValueError, OSError):
        logger.exception("Could not load saved recommendations")
        st.error("Couldn't load your saved songs. Please try again.")
        st.button("Retry saved lists", key=f"retry_lists_{user_id}")
        return
    if not saved:
        st.info("No new songs found. Try Popularity in Options." if saved_method == "genre_v1"
                else "No unheard songs are available for this listener.")
        return
    if len(saved) < requested_k:
        st.caption(f"This saved list contains {len(saved)} of {requested_k} requested songs.")
    results = pd.DataFrame(saved, columns=["Track ID", "Rank", "Song", "Artist", "Score", "Matched genre", "Has preferences"])
    results["Score"] = results["Score"].astype(float)
    if saved_method == "genre_v2":
        results["Reason"] = results.apply(lambda row: "Popular pick · no genre history yet"
            if not row["Has preferences"] else "Popular pick · fills your list"
            if row["Score"] == 0 else f"Matches your {row['Matched genre']} listening"
            if row["Matched genre"] else "Selected from your genre preferences", axis=1)
    elif saved_method == "genre_v1":
        results["Reason"] = "Saved genre recommendation · earlier scoring version"
    elif saved_method == "popularity_v1":
        results["Reason"] = "Popular in the music catalog"
    else:
        results["Reason"] = "From your saved recommendation list"
    track_list(results, user_id, liked_ids, f"recs_{run_id}")
    with st.expander("Details"):
        st.write(f"Run ID: {run_id} · Method: {saved_method} · Generated: {generated_at.astimezone(timezone.utc).isoformat()}")
        st.write(f"History cutoff: {saved_cutoff.astimezone(timezone.utc).isoformat()}")
        st.caption("Scores reflect the scoring rules when a list was generated and aren't confidence percentages. "
                   "Genre v2 weights plays by completion and fills remaining spots with popular songs; "
                   "earlier lists keep their original scores and may use either previous scoring rule. Reasons are rebuilt from the current catalog "
                   "and history before this list's cutoff. Editing those records can change reasons; saved scores stay fixed.")
        st.dataframe(results[["Rank", "Song", "Artist", "Score", "Track ID"]], hide_index=True)


@st.cache_data(ttl=60, show_spinner=False)
def listeners():
    with get_connection(read_only=True) as conn:
        return conn.execute("""SELECT u.user_id, u.username,
            MIN((h.played_when AT TIME ZONE 'UTC')::date),
            MAX((h.played_when AT TIME ZONE 'UTC')::date)
            FROM public.users u LEFT JOIN public.history h ON h.user_id = u.user_id
            GROUP BY u.user_id ORDER BY u.username""").fetchall()


def listener_name(name):
    if name.startswith("synthetic_v1_"):
        return "Demo listener " + name.removeprefix("synthetic_v1_")
    return name.replace("_", " ")


def refresh_data():
    listeners.clear()
    listening_data.clear()


def go_to(page):
    st.session_state["page"] = page


def main():
    st.set_page_config(page_title="Your music", page_icon="🎵", layout="wide")
    st.set_option("client.toolbarMode", "minimal")
    st.html("""<style>
        .stMainBlockContainer {padding-top:2rem;padding-bottom:2rem;max-width:1200px;}
        h1 {font-size:2rem !important;} h2,h3 {font-size:1.3rem !important;}
        [data-testid="stMetric"] {border:1px solid #80808040;border-radius:10px;padding:10px;}
        [data-testid="stMetricValue"] {font-size:1.6rem;}
        [class*="st-key-song_row_"] {border-bottom:1px solid #80808035;padding:4px 0;}
        [class*="st-key-song_row_"] [data-testid="stVerticalBlock"] {gap:.25rem;}
        [class*="st-key-song_row_"] [data-testid="stText"] {font-weight:600;overflow-wrap:anywhere;}
        [class*="st-key-heart_"] button {border:0 !important;border-radius:4px;
            width:44px;height:44px;min-height:44px;padding:0;background:transparent !important;box-shadow:none;
            --heart-filled:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23e11d48' stroke='%23e11d48' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78Z'/%3E%3C/svg%3E");}
        [class*="st-key-heart_"] button::before {content:"";width:26px;height:26px;flex:none;
            background:center / contain no-repeat var(--heart-icon,url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23e11d48' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78Z'/%3E%3C/svg%3E"));}
        [class*="st-key-heart_"] button [data-testid="stMarkdownContainer"] {
            position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
            clip:rect(0,0,0,0);white-space:nowrap;border:0;}
        [class*="st-key-heart_"] button:hover::before {transform:scale(1.1);}
        button:focus-visible {outline:3px solid #60a5fa !important;outline-offset:3px;}
        button[kind="primary"],button[kind="primaryFormSubmit"] {background:#2563eb;border-color:#2563eb;color:white;}
        button[kind="primary"]:hover,button[kind="primaryFormSubmit"]:hover {background:#1d4ed8;border-color:#1d4ed8;}
        [data-testid="stRadioOption"][data-selected] > div > div:first-child,
        [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) > div:first-of-type {
            background-color:#2563eb !important;border-color:#2563eb !important;}
        .st-key-page [role="radiogroup"] {gap:.5rem 1rem;flex-wrap:wrap;}
        .st-key-page label {min-height:44px;padding:4px 8px;border-radius:8px;}
        .st-key-page label:has(input:checked) {background:#2563eb18;box-shadow:inset 0 -2px #2563eb;}
        @media (max-width:640px) {
            .stMainBlockContainer {padding:1rem;}
            [class*="st-key-song_row_"] [data-testid="stHorizontalBlock"] {flex-wrap:nowrap;}
            [class*="st-key-song_row_"] [data-testid="stColumn"]:first-child {flex:1;min-width:0;}
            [class*="st-key-song_row_"] [data-testid="stColumn"]:last-child {flex:0 0 48px;min-width:48px;}
        }
        </style>""")
    st.title("Your music")
    st.caption("Listening stats, new discoveries, and songs you love. · Synthetic-data demo")
    try:
        rows = listeners()
    except (psycopg.Error, ValueError, OSError):
        logger.exception("Could not load listeners")
        st.error("Couldn't connect to your listening data. Please try again.")
        st.button("Retry connection", on_click=refresh_data)
        return
    if not rows:
        st.info("No demo listeners are available yet. Load the project's seed data to get started.")
        return
    users = {row[0]: row[1:] for row in rows}
    default = next((row[0] for row in rows if row[2]), rows[0][0])
    if st.session_state.get("listener") not in users:
        st.session_state["listener"] = default
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    user_id = left.selectbox("Listener", list(users), key="listener", format_func=lambda uid: listener_name(users[uid][0]))
    right.button("Refresh", on_click=refresh_data, help="Load the latest listening data and likes.")
    if st.session_state.get("page") not in PAGES:
        st.session_state["page"] = "Listening"
    section = st.radio("Sections", PAGES, horizontal=True, key="page", label_visibility="collapsed")
    for key in list(st.session_state):
        if key.startswith(("history_search_", "liked_search_", "activity_year_", "activity_day_")):
            st.session_state[key] = st.session_state[key]
    _, first, last = users[user_id]
    now = datetime.now(timezone.utc)
    first, last = min(first or now.date(), now.date()), min(last or now.date(), now.date())
    like_feedback(user_id)

    try:
        if section == "Listening":
            period_key = f"period_{user_id}"
            period = st.session_state.get(period_key, (first, last))
            with st.expander("Date range"):
                left, right = st.columns(2)
                start_date = left.date_input("From", period[0], max_value=now.date(), key=f"from_{user_id}")
                end_date = right.date_input("Through", period[1], max_value=now.date(), key=f"through_{user_id}")
                st.caption("Includes both days, in UTC.")
            if not start_date or not end_date or start_date > end_date:
                st.error("In Date range, choose a start date on or before the end date.")
                return
            st.session_state[period_key] = (start_date, end_date)
            st.caption(f"{start_date:%b %d, %Y} – {end_date:%b %d, %Y}")
            start = datetime.combine(start_date, time.min, timezone.utc)
            end = datetime.combine(end_date + timedelta(days=1), time.min, timezone.utc)
            with st.spinner("Loading your listening…"):
                totals, daily, genres = listening_data(user_id, start, end)
            for column, label, value in zip(st.columns(3),
                ["Minutes", "Plays", "Songs"], [f"{totals[0]:,.0f}", totals[1], totals[2]]):
                column.metric(label, value)
            if not totals[1]:
                st.info("No plays in this date range. Try another range or discover new songs.")
                st.button("Discover songs", type="primary", on_click=go_to, args=("Discover",))

            st.subheader("Listening activity")
            years = sorted(daily.index.year.unique().tolist())
            year_key = f"activity_year_{user_id}"
            if year_key in st.session_state and st.session_state[year_key] not in years:
                del st.session_state[year_key]
            year = st.selectbox("Calendar year", years, index=None if year_key in st.session_state else len(years) - 1, key=year_key) if len(years) > 1 else years[0]
            calendar = daily[daily.index.year == year]
            st.html(activity_calendar(calendar))
            with st.expander("Daily activity details"):
                day_key = f"activity_day_{user_id}"
                lower, upper = calendar.index.min().date(), calendar.index.max().date()
                if day_key in st.session_state and (not st.session_state[day_key] or not lower <= st.session_state[day_key] <= upper):
                    del st.session_state[day_key]
                day = st.date_input("Activity date", None if day_key in st.session_state else upper,
                                    min_value=lower, max_value=upper, key=day_key)
                if day:
                    values = calendar.loc[pd.Timestamp(day)]
                    st.write(f"**{day:%B %d, %Y} (UTC): {int(values['Events'])} plays · {values['Minutes']:.1f} minutes.**")

            if totals[1]:
                songs, artists = listening_data(user_id, start, end, "Top Music")
                library = liked_songs(user_id)
                liked_ids = set(library["Track ID"])
                st.subheader("Your top 5")
                track_list(songs.head(5).drop(columns="Minutes"), user_id, liked_ids, "top", (start_date, end_date))
                with st.expander("More stats"):
                    st.caption("Plays include partial listens. Genre time is split across a song's playlist genre labels.")
                    trend = daily.resample("MS").sum().reset_index()
                    chart = alt.Chart(trend.assign(Month=trend["Date"].dt.strftime("%b %Y"))).mark_line(color=ACCENT, strokeWidth=3, point=True).encode(
                        x=alt.X("Month:O", title=None, sort=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
                        y=alt.Y("Minutes:Q", title="Minutes"),
                        tooltip=["Month:O", alt.Tooltip("Minutes:Q", format=".1f")],
                    ).properties(title="Monthly listening", height=200)
                    st.altair_chart(chart)
                    top_artists = artists.head(10)
                    names = top_artists["Artist"]
                    labels = names.where(~names.duplicated(keep=False), names + " [" + top_artists["Artist ID"].astype(str) + "]")
                    st.altair_chart(ranking_chart(top_artists.assign(Label=labels), "Label", "Plays", f"Top {len(top_artists)} artists"))
                    st.altair_chart(ranking_chart(genres, "Genre", "Minutes", "Your genres"))
                    with st.expander("Chart data"):
                        st.table(trend.assign(Date=trend["Date"].dt.strftime("%b %Y"),
                                              Minutes=trend["Minutes"].round(1)).set_index("Date"))
                        st.table(artists[["Artist", "Plays"]].set_index("Artist"))
                        st.table(genres.assign(Minutes=genres["Minutes"].round(1)).set_index("Genre"))
                with st.expander("Listening history"):
                    history = listening_data(user_id, start, end, "History")
                    search = st.text_input("Search history", placeholder="Song or artist", key=f"history_search_{user_id}")
                    shown = history[(history["Song"] + " " + history["Artist"]).str.contains(search, case=False, regex=False)]
                    track_list(shown, user_id, liked_ids, "history", (start_date, end_date, search))

        elif section == "Discover":
            library = liked_songs(user_id)
            recommendations(user_id, now, set(library["Track ID"]))

        else:
            library = liked_songs(user_id)
            st.subheader(f"Liked songs · {len(library)}")
            st.caption("Tap a red heart to remove a song. Changes save automatically.")
            if library.empty:
                st.info("No likes yet. Discover a song and tap its heart to save it here.")
                st.button("Discover songs", type="primary", on_click=go_to, args=("Discover",))
            else:
                search = st.text_input("Search likes", placeholder="Song or artist", key=f"liked_search_{user_id}")
                shown = library[(library["Song"] + " " + library["Artist"]).str.contains(search, case=False, regex=False)]
                track_list(shown, user_id, set(library["Track ID"]), "library", search)
    except (psycopg.Error, ValueError, OSError):
        logger.exception("Could not load section %s", section)
        st.error(f"Couldn't load {section.lower()}. Please try again.")
        st.button("Retry", on_click=refresh_data, key=f"retry_{section}")


if __name__ == "__main__":
    main()
