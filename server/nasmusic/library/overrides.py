"""Admin tag overrides (schema v5): pure helpers shared by the scanner and the tag editor.

The music library is read-only, so edits are stored in `tag_overrides` and layered over the tags read from
the file. The scanner writes the EFFECTIVE tags into `tracks` (and the artist/album rows they imply), so
search, the API and the AI all see edits without knowing they exist. `tracks.file_tags` keeps what the file
says, so an edit or a revert can be re-applied without re-reading the file."""
from __future__ import annotations

import dataclasses
import json
import re
import sqlite3

from nasmusic.library.tags import TrackTags

# DB field name -> TrackTags attribute (None = not a TrackTags field).
FIELDS: dict[str, str | None] = {
    "title": "title", "artist": "artist", "album_artist": "albumartist", "album": "album",
    "track_no": "track_no", "disc_no": "disc_no", "year": "year", "genre": "genre",
    "lyrics": None, "cover": None,
    # v6: MusicBrainz ids (written by accepted automatic tag suggestions)
    "mb_track": "mb_track", "mb_releasegroup": "mb_releasegroup", "mb_artist": "mb_artist",
    "mb_albumartist": "mb_albumartist",
}
MBID_FIELDS = ("mb_track", "mb_releasegroup", "mb_artist", "mb_albumartist")
TAG_FIELDS = tuple(k for k, v in FIELDS.items() if v)
INT_FIELDS = ("track_no", "disc_no", "year")
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# API (camelCase) <-> DB names.
API_NAMES = {"title": "title", "artist": "artist", "album_artist": "albumArtist", "album": "album",
             "track_no": "trackNo", "disc_no": "discNo", "year": "year", "genre": "genre", "lyrics": "lyrics",
             "cover": "cover", "mb_track": "mbRecordingId", "mb_releasegroup": "mbReleaseGroupId",
             "mb_artist": "mbArtistId", "mb_albumartist": "mbAlbumArtistId"}
FROM_API = {v: k for k, v in API_NAMES.items()}

# What file_tags stores: the overridable tags plus the MBIDs that decide artist/album ids.
_FILE_KEYS = ("title", "artist", "albumartist", "album", "track_no", "disc_no", "year", "genre",
              "mb_track", "mb_artist", "mb_albumartist", "mb_releasegroup")


def file_tags_json(t: TrackTags) -> str:
    return json.dumps({k: getattr(t, k) for k in _FILE_KEYS}, ensure_ascii=False, separators=(",", ":"))


def tags_from_json(raw: str) -> TrackTags:
    d = json.loads(raw)
    return TrackTags(**{k: d.get(k) for k in _FILE_KEYS})


def file_value(t: TrackTags, field: str):
    attr = FIELDS.get(field)
    return getattr(t, attr) if attr else None


def clean_value(field: str, value):
    """Validate/normalise an override value. Strings are trimmed ('' = clear the tag -> None)."""
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}")
    if value is None:
        return None
    if field in INT_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"{API_NAMES[field]} must be a number")
        try:
            n = int(str(value).strip()) if str(value).strip() else None
        except ValueError as e:
            raise ValueError(f"{API_NAMES[field]} must be a number") from e
        if n is not None and not (0 <= n <= (9999 if field == "year" else 999)):
            raise ValueError(f"{API_NAMES[field]} out of range")
        return n
    if not isinstance(value, str):
        raise ValueError(f"{API_NAMES[field]} must be text")
    if field in MBID_FIELDS:
        v = value.strip().lower()
        if not v:
            return None
        if not _MBID.fullmatch(v):
            raise ValueError(f"{API_NAMES[field]} must be a MusicBrainz id")
        return v
    limit = 200_000 if field == "lyrics" else 500
    v = value if field == "lyrics" else " ".join(value.split())
    if len(v) > limit:
        raise ValueError(f"{API_NAMES[field]} is too long")
    return v if v.strip() else None


def apply(t: TrackTags, ov: dict | None) -> TrackTags:
    """File tags + overrides -> effective tags. An overridden artist/album artist drops the file's MBID
    for it (the MBID belongs to the old name) unless the MBID is overridden too."""
    if not ov:
        return t
    changes = {FIELDS[f]: v for f, v in ov.items() if FIELDS.get(f)}
    if "artist" in ov and "mb_artist" not in ov:
        changes["mb_artist"] = None
    if "album_artist" in ov and "mb_albumartist" not in ov:
        changes["mb_albumartist"] = None
    return dataclasses.replace(t, **changes)


def load_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """{track_id: {field: value}} for the tag fields (not lyrics/cover)."""
    out: dict[str, dict] = {}
    q = f"SELECT track_id, field, value FROM tag_overrides WHERE field IN ({','.join('?' * len(TAG_FIELDS))})"
    for r in conn.execute(q, TAG_FIELDS):
        out.setdefault(r[0], {})[r[1]] = json.loads(r[2])
    return out


def load(conn: sqlite3.Connection, track_id: str) -> dict:
    return {r[0]: json.loads(r[1]) for r in conn.execute(
        "SELECT field, value FROM tag_overrides WHERE track_id = ?", (track_id,))}


def album_covers(conn: sqlite3.Connection) -> dict[str, str]:
    """{album_id: art_path sentinel} for albums with an uploaded cover (the most used one wins)."""
    out: dict[str, str] = {}
    for r in conn.execute(
            "SELECT t.album_id, o.value, COUNT(*) AS n FROM tag_overrides o JOIN tracks t ON t.id = o.track_id "
            "WHERE o.field = 'cover' AND t.missing = 0 GROUP BY t.album_id, o.value ORDER BY n DESC, o.value"):
        out.setdefault(r[0], f"@upload/{json.loads(r[1])}.jpg")
    return out


def lyrics_override(conn: sqlite3.Connection, track_id: str) -> str | None:
    r = conn.execute("SELECT value FROM tag_overrides WHERE track_id = ? AND field = 'lyrics'", (track_id,)).fetchone()
    return json.loads(r[0]) if r else None
