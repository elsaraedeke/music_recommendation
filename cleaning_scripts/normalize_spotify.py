"""Build deterministic, validated catalog relations from cleaned playlist rows."""
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "cleaned_data"


def most_common(series):
    counts = series.value_counts()
    choices = counts[counts == counts.max()].index
    return sorted(choices)[0]


def normalize(data_dir=DATA_DIR):
    data_dir = Path(data_dir)
    df = pd.read_csv(data_dir / "spotify_cleaned.csv")
    required = {
        "track_id", "track_name", "track_artist", "track_popularity",
        "track_album_id", "track_album_name", "track_album_release_date",
        "playlist_genre", "playlist_subgenre", "duration_ms",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing cleaned catalog columns: {sorted(missing)}")
    if df.empty or df[list(required)].isnull().any().any():
        raise ValueError("The cleaned catalog must contain non-null required values")
    for column in ("duration_ms", "track_popularity"):
        numeric = pd.to_numeric(df[column], errors="coerce")
        if numeric.isnull().any() or numeric.mod(1).ne(0).any():
            raise ValueError(f"{column} must contain integers")
        df[column] = numeric.astype("int64")
    artists = pd.DataFrame({"artist_name": sorted(df["track_artist"].unique())})
    artists.insert(0, "artist_id", range(1, len(artists) + 1))
    artist_lookup = dict(zip(artists["artist_name"], artists["artist_id"]))
    genres = pd.DataFrame({"genre_name": sorted(df["playlist_genre"].unique())})
    genres.insert(0, "genre_id", range(1, len(genres) + 1))
    genre_lookup = dict(zip(genres["genre_name"], genres["genre_id"]))

    # Album artist is the modal track artist over distinct tracks; ties sort by name.
    album_artists = df[["track_album_id", "track_id", "track_artist"]].drop_duplicates()
    album_artist_choice = (
        album_artists.groupby("track_album_id")["track_artist"].agg(most_common).reset_index()
    )
    # Keep the established first-row metadata policy, preserving source file order.
    albums = df[["track_album_id", "track_album_name", "track_album_release_date"]].drop_duplicates(
        subset=["track_album_id"]
    ).copy()
    albums = albums.merge(album_artist_choice, on="track_album_id", how="left", validate="one_to_one")
    albums["artist_id"] = albums["track_artist"].map(artist_lookup)
    albums = albums.rename(columns={
        "track_album_id": "album_id", "track_album_name": "album_name",
        "track_album_release_date": "release_date",
    })[["album_id", "album_name", "release_date", "artist_id"]].sort_values("album_id").reset_index(drop=True)

    # A primary label supports the original single-genre query examples.
    primary_genres = df.groupby("track_id")["playlist_genre"].agg(most_common).reset_index()
    subgenre_candidates = df.merge(primary_genres, on="track_id", how="inner", suffixes=("", "_selected"))
    subgenre_candidates = subgenre_candidates[
        subgenre_candidates["playlist_genre"] == subgenre_candidates["playlist_genre_selected"]
    ]
    primary_subgenres = subgenre_candidates.groupby("track_id")["playlist_subgenre"].agg(most_common).reset_index()
    songs = df.drop_duplicates(subset=["track_id"])[[
        "track_id", "track_name", "track_artist", "track_popularity", "track_album_id", "duration_ms",
    ]].copy()
    songs = songs.merge(primary_genres, on="track_id", how="left", validate="one_to_one")
    songs = songs.merge(primary_subgenres, on="track_id", how="left", validate="one_to_one")
    songs["artist_id"] = songs["track_artist"].map(artist_lookup)
    songs["genre_id"] = songs["playlist_genre"].map(genre_lookup)
    songs = songs.rename(columns={"track_album_id": "album_id", "playlist_subgenre": "subgenre"})[[
        "track_id", "track_name", "artist_id", "track_popularity", "album_id", "genre_id", "subgenre", "duration_ms",
    ]].sort_values("track_id").reset_index(drop=True)

    # Retain every distinct observed genre, rather than only the primary label.
    song_genres = df[["track_id", "playlist_genre"]].drop_duplicates().copy()
    song_genres["genre_id"] = song_genres["playlist_genre"].map(genre_lookup)
    song_genres = song_genres[["track_id", "genre_id"]].sort_values(["track_id", "genre_id"]).reset_index(drop=True)
    tables = {"artists": artists, "albums": albums, "genres": genres, "songs": songs, "song_genres": song_genres}
    keys = {"artists": ["artist_id"], "albums": ["album_id"], "genres": ["genre_id"],
            "songs": ["track_id"], "song_genres": ["track_id", "genre_id"]}
    for name, table in tables.items():
        if table.empty or table.isnull().any().any() or table.duplicated(keys[name]).any():
            raise ValueError(f"Empty, null, or duplicate-key rows in {name}")
    for table, column, parent, parent_column in [
        (albums, "artist_id", artists, "artist_id"), (songs, "artist_id", artists, "artist_id"),
        (songs, "album_id", albums, "album_id"), (songs, "genre_id", genres, "genre_id"),
        (song_genres, "track_id", songs, "track_id"), (song_genres, "genre_id", genres, "genre_id"),
    ]:
        if not table[column].isin(parent[parent_column]).all():
            raise ValueError(f"Invalid reference: {column}")
    if not songs["duration_ms"].gt(0).all() or not songs["track_popularity"].between(0, 100).all():
        raise ValueError("Invalid song duration or popularity")
    if len(songs) != df["track_id"].nunique():
        raise ValueError("A cleaned song was lost during normalization")
    if not songs[["track_id", "genre_id"]].merge(song_genres, how="left", indicator=True)["_merge"].eq("both").all():
        raise ValueError("A primary genre is missing from song_genres")
    for name, table in tables.items():
        table.to_csv(data_dir / f"{name}.csv", index=False)
    return {name: len(table) for name, table in tables.items()}


if __name__ == "__main__":
    for name, count in normalize().items():
        print(f"{name}: {count}")
    print("Normalization complete; key, reference, and value validations passed.")
