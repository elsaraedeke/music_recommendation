-- queries that return data, little analysis

-- get total minutes listened overall
SELECT SUM(listened_ms) / 60000 AS total_min
FROM history;


--get total times and minutes an individual song is played
--replace HAVING equality clause with intended search track_id
SELECT track_id, 
	COUNT(track_id) AS times_played, 
	SUM(listened_ms) / 60000 AS total_min
FROM history
GROUP BY track_id
HAVING track_id = '0017A6SJgTbfQVU2EtsPNo';


--get song with most plays
SELECT track_id, 
	COUNT(track_id) AS times_played, 
	SUM(listened_ms) / 60000 AS total_min
FROM history
GROUP BY track_id
ORDER BY times_played DESC
LIMIT 1;


--get artist with most plays
SELECT a.artist_id, 
	COUNT(h.track_id) AS times_played, 
	SUM(h.listened_ms) / 60000 AS total_min
FROM artists a
JOIN songs s
	ON a.artist_id = s.artist_id
JOIN history h
	ON s.track_id = h.track_id
GROUP BY a.artist_id
ORDER BY times_played DESC
LIMIT 1;


--get genre with most plays
SELECT g.genre_id, 
	COUNT(h.track_id) AS times_played, 
	SUM(h.listened_ms) / 60000 AS total_min
FROM genres g
JOIN songs s
	ON g.genre_id = s.genre_id
JOIN history h
	ON s.track_id = h.track_id
GROUP BY g.genre_id
ORDER BY times_played DESC
LIMIT 1;