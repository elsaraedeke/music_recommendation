-- Standalone global examples: all users and dates, including partial listens.
-- The dashboard's parameterized user/date queries are in app.listening_data.
-- Divide by 60000.0 to retain fractional minutes; an empty total is zero.

-- 1. Total actual listening minutes.
SELECT COALESCE(SUM(listened_ms), 0) / 60000.0 AS total_min
FROM public.history;

-- 2. Plays/minutes for one track. Replace this example ID as needed.
-- An unheard or unknown track produces no grouped row.
SELECT track_id, COUNT(*) AS times_played,
       SUM(listened_ms) / 60000.0 AS total_min
FROM public.history
WHERE track_id = '0017A6SJgTbfQVU2EtsPNo'
GROUP BY track_id;

-- 3. Most-played track; listening time and ID break ties deterministically.
SELECT track_id, COUNT(*) AS times_played,
       SUM(listened_ms) / 60000.0 AS total_min
FROM public.history
GROUP BY track_id
ORDER BY times_played DESC, total_min DESC, track_id
LIMIT 1;

-- 4. Most-played artist.
SELECT a.artist_id, COUNT(*) AS times_played,
       SUM(h.listened_ms) / 60000.0 AS total_min
FROM public.artists a
JOIN public.songs s ON a.artist_id = s.artist_id
JOIN public.history h ON s.track_id = h.track_id
GROUP BY a.artist_id
ORDER BY times_played DESC, total_min DESC, a.artist_id
LIMIT 1;

-- 5. Most-played PRIMARY genre: each event belongs to songs.genre_id once.
-- This differs from the all-genre fractional attribution below.
SELECT g.genre_id, COUNT(*) AS times_played,
       SUM(h.listened_ms) / 60000.0 AS total_min
FROM public.genres g
JOIN public.songs s ON g.genre_id = s.genre_id
JOIN public.history h ON s.track_id = h.track_id
GROUP BY g.genre_id
ORDER BY times_played DESC, total_min DESC, g.genre_id
LIMIT 1;

-- 6. ALL-genre play share. Split one play across its labels, preventing
-- double counting. Missing bridge labels contribute to Uncategorized.
-- The dashboard splits listening MINUTES; this example splits PLAY counts.
-- Rounded percentages may differ slightly from 100 in total.
WITH genre_counts AS (
    SELECT track_id, COUNT(*) AS n
    FROM public.song_genres GROUP BY track_id
), contributions AS (
    SELECT sg.genre_id, SUM(1.0 / COALESCE(gc.n, 1)) AS attributed_plays
    FROM public.history h
    LEFT JOIN public.song_genres sg ON sg.track_id = h.track_id
    LEFT JOIN genre_counts gc ON gc.track_id = h.track_id
    GROUP BY sg.genre_id
)
SELECT c.genre_id, COALESCE(g.genre_name, 'Uncategorized') AS genre_name,
       c.attributed_plays,
       ROUND(100.0 * c.attributed_plays / SUM(c.attributed_plays) OVER (), 2) AS pct_of_plays
FROM contributions c LEFT JOIN public.genres g ON g.genre_id = c.genre_id
ORDER BY pct_of_plays DESC, genre_name, c.genre_id;
