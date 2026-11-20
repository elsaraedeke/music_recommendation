sql_view = '''
    CREATE VIEW recommendation_candidates AS
    SELECT s.*
    FROM songs s
    WHERE NOT EXISTS (
        SELECT 1
        FROM history h
        WHERE h.track_id = s.track_id
            AND h.user_id = %s
    )
    ORDER BY s.genre_id;
    '''