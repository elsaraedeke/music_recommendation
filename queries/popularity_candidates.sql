SELECT s.track_id, s.track_name, a.artist_name, s.track_popularity::numeric AS score
FROM public.songs s
JOIN public.artists a ON a.artist_id = s.artist_id
WHERE EXISTS (SELECT 1 FROM public.users WHERE user_id = %(user_id)s)
  AND NOT EXISTS (
      SELECT 1 FROM public.history h
      WHERE h.user_id = %(user_id)s AND h.track_id = s.track_id
        AND h.played_when < %(cutoff)s::timestamptz
  )
ORDER BY score DESC, s.track_id
LIMIT %(k)s;
