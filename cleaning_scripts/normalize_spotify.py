
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "cleaned_data"

df = pd.read_csv(DATA_DIR / "spotify_cleaned.csv")

def most_common(series):
    counts = series.value_counts()
    choices = counts[counts == counts.max()].index
    return sorted(choices)[0]

artists = pd.DataFrame({
    "artist_name": sorted(df["track_artist"].unique())
})

artists.insert(0, "artist_id", range(1, len(artists) + 1))

artist_lookup = dict(zip(
    artists["artist_name"],
    artists["artist_id"]
))

genres = pd.DataFrame({
    "genre_name": sorted(df["playlist_genre"].unique())
})

genres.insert(0, "genre_id", range(1, len(genres) + 1))

genre_lookup = dict(zip(
    genres["genre_name"],
    genres["genre_id"]
))

album_artists = df[
    ["track_album_id", "track_id", "track_artist"]
].drop_duplicates()

album_artist_choice = (
    album_artists.groupby("track_album_id")["track_artist"]
    .agg(most_common)
    .reset_index()
)

albums = df[
    [
        "track_album_id",
        "track_album_name",
        "track_album_release_date"
    ]
].drop_duplicates(subset=["track_album_id"]).copy()

albums = albums.merge(
    album_artist_choice,
    on="track_album_id",
    how="left",
    validate="one_to_one"
)

albums["artist_id"] = albums["track_artist"].map(artist_lookup)

albums = albums.rename(columns={
    "track_album_id": "album_id",
    "track_album_name": "album_name",
    "track_album_release_date": "release_date"
})

albums = albums[
    ["album_id", "album_name", "release_date", "artist_id"]
].sort_values("album_id").reset_index(drop=True)

song_genres = (
    df.groupby("track_id")["playlist_genre"]
    .agg(most_common)
    .reset_index()
)

subgenre_candidates = df.merge(
    song_genres,
    on="track_id",
    how="inner",
    suffixes=("", "_selected")
)

subgenre_candidates = subgenre_candidates[
    subgenre_candidates["playlist_genre"] ==
    subgenre_candidates["playlist_genre_selected"]
]

song_subgenres = (
    subgenre_candidates.groupby("track_id")["playlist_subgenre"]
    .agg(most_common)
    .reset_index()
)

songs = df.drop_duplicates(subset=["track_id"])[
    [
        "track_id",
        "track_name",
        "track_artist",
        "track_popularity",
        "track_album_id"
    ]
].copy()

songs = songs.merge(
    song_genres,
    on="track_id",
    how="left",
    validate="one_to_one"
)

songs = songs.merge(
    song_subgenres,
    on="track_id",
    how="left",
    validate="one_to_one"
)

songs["artist_id"] = songs["track_artist"].map(artist_lookup)
songs["genre_id"] = songs["playlist_genre"].map(genre_lookup)

songs = songs.rename(columns={
    "track_album_id": "album_id",
    "playlist_subgenre": "subgenre"
})

songs = songs[
    [
        "track_id",
        "track_name",
        "artist_id",
        "track_popularity",
        "album_id",
        "genre_id",
        "subgenre"
    ]
].sort_values("track_id").reset_index(drop=True)

assert artists["artist_id"].is_unique
assert albums["album_id"].is_unique
assert genres["genre_id"].is_unique
assert songs["track_id"].is_unique

assert albums["artist_id"].isin(artists["artist_id"]).all()
assert songs["artist_id"].isin(artists["artist_id"]).all()
assert songs["album_id"].isin(albums["album_id"]).all()
assert songs["genre_id"].isin(genres["genre_id"]).all()

for name, table in [
    ("artists", artists),
    ("albums", albums),
    ("genres", genres),
    ("songs", songs)
]:
    assert not table.isnull().any().any(), f"Null values in {name}"

assert len(songs) == df["track_id"].nunique()

artists.to_csv(DATA_DIR / "artists.csv", index=False)
albums.to_csv(DATA_DIR / "albums.csv", index=False)
genres.to_csv(DATA_DIR / "genres.csv", index=False)
songs.to_csv(DATA_DIR / "songs.csv", index=False)

print("Normalization complete!")
print("Artists:", len(artists))
print("Albums:", len(albums))
print("Genres:", len(genres))
print("Songs:", len(songs))
print("All primary and foreign key validations passed.")
