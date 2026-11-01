import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_FILE = BASE_DIR / "data" / "raw" / "spotify_songs.csv"
OUTPUT_DIR = BASE_DIR / "data" / "cleaned_data"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(RAW_FILE)

print("===== DATASET OVERVIEW =====")
print("Rows:", df.shape[0])
print("Columns:", df.shape[1])

print("\n===== FIRST 5 ROWS =====")
print(df.head().to_string())

print("\n===== COLUMN INFORMATION =====")
df.info()

print("\n===== MISSING VALUES =====")
print(df.isnull().sum().to_string())

print("\n===== DUPLICATES =====")
print("Exact duplicate rows:", df.duplicated().sum())
print("Repeated track IDs:", df.duplicated(subset=["track_id"]).sum())
print("Unique tracks:", df["track_id"].nunique())

print("\n===== GENRES =====")
print(df["playlist_genre"].value_counts())

print("\n===== SUBGENRES =====")
print(df["playlist_subgenre"].value_counts())

print("\n===== RELEASE DATE EXAMPLES =====")
print(df["track_album_release_date"].value_counts().head(15))

print("\n===== NUMERIC STATISTICS =====")
print(df.describe().to_string())

important_cols = [
    "track_name",
    "track_artist",
    "track_album_name"
]

missing_rows = df[df[important_cols].isna().any(axis=1)]

print("\n===== ROWS WITH MISSING VALUES =====")
print(
    missing_rows[
        [
            "track_id",
            "track_name",
            "track_artist",
            "track_album_id",
            "track_album_name"
        ]
    ].to_string(index=False)
)

print("\nTotal affected rows:", len(missing_rows))

duplicate_tracks = df[
    df.duplicated(subset=["track_id"], keep=False)
].sort_values("track_id")

print("\n===== EXAMPLE REPEATED TRACK IDS =====")
print(
    duplicate_tracks[
        [
            "track_id",
            "track_name",
            "track_artist",
            "playlist_name",
            "playlist_genre"
        ]
    ].head(20).to_string(index=False)
)

conflicts = df.groupby("track_id").agg(
    name_count=("track_name", "nunique"),
    artist_count=("track_artist", "nunique"),
    album_count=("track_album_id", "nunique")
)

conflicting_tracks = conflicts[
    (conflicts["name_count"] > 1) |
    (conflicts["artist_count"] > 1) |
    (conflicts["album_count"] > 1)
]

print("\n===== TRACK METADATA CONFLICTS =====")
print("Track IDs with conflicting metadata:", len(conflicting_tracks))
print(conflicting_tracks.head(10))

release_dates = df["track_album_release_date"].astype(str)

date_formats = {
    "YYYY": release_dates.str.fullmatch(r"\d{4}").sum(),
    "YYYY-MM": release_dates.str.fullmatch(r"\d{4}-\d{2}").sum(),
    "YYYY-MM-DD": release_dates.str.fullmatch(r"\d{4}-\d{2}-\d{2}").sum()
}

print("\n===== RELEASE DATE FORMATS =====")
print(date_formats)

recognized = sum(date_formats.values())
print("Unrecognized date formats:", len(df) - recognized)

playlist_conflicts = df.groupby("playlist_id").agg(
    genre_count=("playlist_genre", "nunique"),
    subgenre_count=("playlist_subgenre", "nunique")
)

print("\n===== PLAYLIST GENRE CONFLICTS =====")
print(
    "Playlists with multiple genres:",
    (playlist_conflicts["genre_count"] > 1).sum()
)
print(
    "Playlists with multiple subgenres:",
    (playlist_conflicts["subgenre_count"] > 1).sum()
)

clean_df = df.copy()

print("\n===== STARTING DATA CLEANING =====")
print("Original rows:", len(clean_df))

required_columns = [
    "track_id",
    "track_name",
    "track_artist",
    "track_album_id",
    "track_album_name"
]

clean_df = clean_df.dropna(subset=required_columns).copy()

print("Rows after removing missing values:", len(clean_df))

text_columns = [
    "track_id",
    "track_name",
    "track_artist",
    "track_album_id",
    "track_album_name",
    "track_album_release_date",
    "playlist_id",
    "playlist_name",
    "playlist_genre",
    "playlist_subgenre"
]

for column in text_columns:
    clean_df[column] = clean_df[column].str.strip()

for column in required_columns:
    clean_df = clean_df[
        clean_df[column].notna() &
        clean_df[column].ne("")
    ].copy()

bounded_columns = [
    "danceability",
    "energy",
    "speechiness",
    "acousticness",
    "instrumentalness",
    "liveness",
    "valence"
]

invalid_numeric = pd.Series(False, index=clean_df.index)

for column in bounded_columns:
    invalid_numeric |= ~clean_df[column].between(0, 1)

invalid_numeric |= ~clean_df["track_popularity"].between(0, 100)
invalid_numeric |= ~clean_df["key"].between(0, 11)
invalid_numeric |= ~clean_df["mode"].isin([0, 1])
invalid_numeric |= clean_df["duration_ms"] <= 0
invalid_numeric |= clean_df["tempo"] < 0

print("Rows with invalid numeric values:", invalid_numeric.sum())

if invalid_numeric.any():
    print(clean_df.loc[invalid_numeric].to_string())
    raise ValueError("Invalid numeric records need review.")

clean_df["release_date_precision"] = (
    clean_df["track_album_release_date"]
    .str.count("-")
    .map({0: "year", 1: "month", 2: "day"})
)

def valid_release_date(value):
    try:
        if len(value) == 4:
            pd.to_datetime(value, format="%Y", errors="raise")
        elif len(value) == 7:
            pd.to_datetime(value, format="%Y-%m", errors="raise")
        elif len(value) == 10:
            pd.to_datetime(value, format="%Y-%m-%d", errors="raise")
        else:
            return False
        return True
    except (ValueError, TypeError):
        return False

invalid_dates = ~clean_df[
    "track_album_release_date"
].apply(valid_release_date)

print("Invalid release dates:", invalid_dates.sum())

if invalid_dates.any():
    raise ValueError("Invalid release dates need review.")

clean_df = clean_df.drop_duplicates()

clean_df.to_csv(
    OUTPUT_DIR / "spotify_cleaned.csv",
    index=False
)

print("\n===== CLEANING COMPLETE =====")
print("Original rows:", len(df))
print("Cleaned rows:", len(clean_df))
print("Unique tracks:", clean_df["track_id"].nunique())
print("Cleaned CSV saved successfully!")

playlist_check = clean_df.groupby("playlist_id").agg(
    genre_count=("playlist_genre", "nunique"),
    subgenre_count=("playlist_subgenre", "nunique")
)

conflicting_ids = playlist_check[
    (playlist_check["genre_count"] > 1) |
    (playlist_check["subgenre_count"] > 1)
].index

print("\n===== CONFLICTING PLAYLIST GENRES =====")

print(
    clean_df[
        clean_df["playlist_id"].isin(conflicting_ids)
    ][
        [
            "playlist_id",
            "playlist_name",
            "playlist_genre",
            "playlist_subgenre"
        ]
    ]
    .drop_duplicates()
    .sort_values("playlist_id")
    .to_string(index=False)
)

print("\n===== SHORT TRACKS =====")

print(
    clean_df.loc[
        clean_df["duration_ms"] < 30000,
        [
            "track_id",
            "track_name",
            "track_artist",
            "duration_ms"
        ]
    ].to_string(index=False)
)
