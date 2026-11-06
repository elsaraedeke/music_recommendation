WITH genre_counts AS (
    SELECT track_id, COUNT(*) AS n FROM public.song_genres GROUP BY track_id
), preferences AS (
    -- Split listening time equally across each song's genres.
    SELECT sg.genre_id, SUM(h.listened_ms::numeric / gc.n) AS listening_time
    FROM public.history h
    JOIN public.song_genres sg ON sg.track_id = h.track_id
    JOIN genre_counts gc ON gc.track_id = h.track_id
    WHERE h.user_id = %(user_id)s AND h.played_when < %(cutoff)s::timestamptz
      AND h.listened_ms > 0
    GROUP BY sg.genre_id
), weights AS (
    SELECT genre_id, listening_time / SUM(listening_time) OVER () AS weight
    FROM preferences
), scored AS (
    SELECT s.track_id, s.track_name, s.artist_id, s.track_popularity,
           CASE WHEN EXISTS (SELECT 1 FROM weights)
                THEN AVG(COALESCE(w.weight, 0))
                ELSE s.track_popularity::numeric END AS score
    FROM public.songs s
    LEFT JOIN public.song_genres sg ON sg.track_id = s.track_id
    LEFT JOIN weights w ON w.genre_id = sg.genre_id
    WHERE EXISTS (SELECT 1 FROM public.users WHERE user_id = %(user_id)s)
      AND NOT EXISTS (
          SELECT 1 FROM public.history h
          WHERE h.user_id = %(user_id)s AND h.track_id = s.track_id
            AND h.played_when < %(cutoff)s::timestamptz
      )
    GROUP BY s.track_id
)
SELECT s.track_id, s.track_name, a.artist_name, s.score
FROM scored s
JOIN public.artists a ON a.artist_id = s.artist_id
WHERE s.score > 0 OR NOT EXISTS (SELECT 1 FROM weights)
ORDER BY s.score DESC, s.track_popularity DESC, s.track_id
LIMIT %(k)s;
