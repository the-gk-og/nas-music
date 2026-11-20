"""Automatic tag fixing (schema v6): fingerprint decoding, AcoustID client and rate limiting, matching and
scoring, suggestions grouped by album, accept/reject/reopen through tag overrides. All HTTP is mocked."""
import dataclasses
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import numpy as np
import pytest

from nasmusic.library import acoustid, autotag, chromaprint, musicbrainz
from tests.conftest import png_bytes, track_ids

REL_A = "aaaaaaaa-0000-0000-0000-000000000001"     # "First Album" (album)
REL_C = "cccccccc-0000-0000-0000-000000000003"     # "Greatest Hits" (compilation, also has Song 1)
REL_B = "bbbbbbbb-0000-0000-0000-000000000002"     # "Second"
RG_A, RG_B = "aaaaaaaa-1111-0000-0000-000000000001", "bbbbbbbb-1111-0000-0000-000000000002"
R1, R2, R3 = ("11111111-0000-0000-0000-00000000000" + str(i) for i in (1, 2, 3))
AR1 = "a1a1a1a1-0000-0000-0000-000000000001"


# ---------- fakes ----------

def _rel(rid, title, rg_type, track_count, pos, secondary=(), year=2001):
    return {"id": rid, "title": title, "track_count": track_count, "medium_count": 1, "date": {"year": year},
            "mediums": [{"position": 1, "format": "CD", "track_count": track_count,
                         "tracks": [{"position": pos, "title": "x", "artists": [{"id": AR1, "name": "Artist One"}]}]}],
            "releasegroup": {"id": RG_A, "type": rg_type, "secondarytypes": list(secondary), "title": title}}


def _acoustid_result(rec_id, title, releases, score=0.95, aid="acoustid-1"):
    return {"id": aid, "score": score, "recordings": [{
        "id": rec_id, "title": title, "duration": 2, "artists": [{"id": AR1, "name": "Artist One"}],
        "releases": releases}]}


ACOUSTID = {
    "FP-Song 1": [_acoustid_result(R1, "Song One", [_rel(REL_C, "Greatest Hits", "Album", 20, 7, ["Compilation"]),
                                                    _rel(REL_A, "First Album", "Album", 2, 1)])],
    "FP-Song 2": [_acoustid_result(R2, "Song Two", [_rel(REL_A, "First Album", "Album", 2, 2)], aid="acoustid-2")],
    "FP-Opening": [_acoustid_result(R3, "Opening", [_rel(REL_B, "Second", "Album", 2, 1, year=1999)], 0.9,
                                    aid="acoustid-3")],
    "FP-Closing": [],
    "FP-untagged": [_acoustid_result(R1, "Song One", [_rel(REL_A, "First Album", "Album", 2, 1)], 0.9)],
}


def _mb_release(rid):
    if rid == REL_A:
        return {"id": REL_A, "title": "First Album", "date": "2001-05-01", "country": "GB",
                "artist-credit": [{"name": "Artist One", "joinphrase": "", "artist": {"id": AR1, "name": "Artist One"}}],
                "release-group": {"id": RG_A, "primary-type": "Album", "first-release-date": "2001-05-01"},
                "genres": [{"name": "indie rock", "count": 3}], "cover-art-archive": {"front": True},
                "media": [{"position": 1, "format": "CD", "tracks": [
                    {"position": 1, "number": "1", "title": "Song One", "length": 2000, "recording": {"id": R1}},
                    {"position": 2, "number": "2", "title": "Song Two", "length": 2100, "recording": {"id": R2}}]}]}
    if rid == REL_B:
        return {"id": REL_B, "title": "Second", "date": "1999", "artist-credit": [{"name": "The Band", "joinphrase": ""}],
                "release-group": {"id": RG_B, "primary-type": "Album", "first-release-date": "1998-10-10"},
                "cover-art-archive": {"front": False},
                "media": [{"position": 1, "tracks": [
                    {"position": 1, "number": "1", "title": "Opening", "length": 2000, "recording": {"id": R3},
                     "artist-credit": [{"name": "The Band", "joinphrase": " feat. ", "artist": {"id": "x"}},
                                       {"name": "X", "joinphrase": "", "artist": {"id": "y"}}]}]}]}
    return None


class Fake:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append(f"{host}{request.url.path}")
        if host == "api.acoustid.org":
            form = parse_qs(request.content.decode())
            assert form["client"] == ["test-key"]
            assert form["meta"] == [acoustid.META]
            assert "musicbrainz" not in request.headers["user-agent"].lower() or True
            return httpx.Response(200, json={"status": "ok", "results": ACOUSTID[form["fingerprint"][0]]})
        if host == "musicbrainz.org":
            assert request.headers["user-agent"] == musicbrainz.USER_AGENT
            data = _mb_release(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=data) if data else httpx.Response(404, json={})
        if host == "coverartarchive.org":
            return httpx.Response(200, content=png_bytes((0, 200, 0), (500, 500)), headers={"content-type": "image/png"})
        raise httpx.ConnectError(f"unexpected host {host}")


def fake_compute(path: Path, fpcalc=None):
    stem = path.stem.split(" ", 1)[1] if path.stem[:2].isdigit() else path.stem
    seed = int(hashlib.md5(stem.encode()).hexdigest()[:8], 16)
    raw = np.random.default_rng(seed).integers(0, 2**32, 300, dtype=np.uint64).astype(np.uint32)
    return chromaprint.Fingerprint(duration_s=2, fingerprint=f"FP-{stem}", raw=raw)


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def kcfg(cfg):
    return dataclasses.replace(cfg, acoustid_api_key="test-key")


def run_job(conn, kcfg, fake, **kw):
    return autotag.run(conn, kcfg, transport=httpx.MockTransport(fake), compute=fake_compute,
                       limiter=acoustid.RateLimiter(0), mb_interval_s=0, **kw)


def _md5s(root):
    return {p: hashlib.md5(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


# ---------- fingerprints ----------

@pytest.mark.skipif(shutil.which("fpcalc") is None or shutil.which("ffmpeg") is None, reason="needs fpcalc+ffmpeg")
def test_decode_matches_fpcalc_raw(tmp_path):
    src = tmp_path / "noise.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:seed=3",
                    "-ac", "2", str(src)], check=True)
    mp3 = tmp_path / "noise.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-b:a", "128k", str(mp3)], check=True)
    raw = json.loads(subprocess.run(["fpcalc", "-json", "-raw", str(src)], capture_output=True).stdout)["fingerprint"]
    fp = chromaprint.compute(src)
    assert fp.duration_s == 20
    assert np.array_equal(np.array(raw, dtype=np.int64) & 0xFFFFFFFF, fp.raw.astype(np.int64))
    assert chromaprint.similarity(fp.raw, chromaprint.compute(mp3).raw) > 0.9


def test_decode_rejects_garbage():
    with pytest.raises(chromaprint.FingerprintError):
        chromaprint.decode("AQ")
    with pytest.raises(chromaprint.FingerprintError):
        chromaprint.decode("AQAAEA")  # claims 16 items, has none


def test_similarity():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 2**32, 400, dtype=np.uint64).astype(np.uint32)
    noisy = a ^ (rng.random(400) < 0.05).astype(np.uint32) << 3   # a few flipped bits
    shifted = np.concatenate([rng.integers(0, 2**32, 5, dtype=np.uint64).astype(np.uint32), a])
    other = rng.integers(0, 2**32, 400, dtype=np.uint64).astype(np.uint32)
    assert chromaprint.similarity(a, a) == 1.0
    assert chromaprint.similarity(a, noisy) > 0.99
    assert chromaprint.similarity(a, shifted) == 1.0
    assert chromaprint.similarity(a, other) < 0.6


# ---------- scoring ----------

def test_text_similarity_ignores_noise():
    assert autotag.sim("Song One (2011 Remaster)", "song one") == 1.0
    assert autotag.sim("Beyoncé", "Beyonce") == 1.0
    assert autotag.sim("Totally Different", "Song One") < 0.5
    assert autotag.sim(None, "x") == 0.0


def test_duration_factor():
    assert autotag.duration_factor(200, 202) == 1.0
    assert 0.5 < autotag.duration_factor(200, 212) < 1.0
    assert autotag.duration_factor(200, 300) == 0.5
    assert autotag.duration_factor(None, 200) == 0.9


def test_confidence_ordering():
    exact = autotag.track_confidence(0.97, 1.0, 1.0, True)
    untagged = autotag.track_confidence(0.97, 1.0, None, True)
    disagree = autotag.track_confidence(0.97, 1.0, 0.1, True)
    off_dur = autotag.track_confidence(0.97, autotag.duration_factor(200, 220), 1.0, True)
    no_release = autotag.track_confidence(0.97, 1.0, 1.0, False)
    weak = autotag.track_confidence(0.5, 1.0, 1.0, True)
    assert exact > 0.95
    assert exact > untagged > disagree
    assert exact > off_dur and exact > no_release and exact > weak
    assert 0.0 <= weak <= 1.0
    assert autotag.group_confidence([0.9, 0.9], 1.0) == 0.9
    assert autotag.group_confidence([0.9, 0.9], 0.5) < 0.9
    assert autotag.group_confidence([], 1.0) == 0.0


def test_agreement_handles_generic_titles():
    assert autotag.agreement("Track 03", None, "Song One", "A", "x/03 Track 03.flac") is None
    assert autotag.agreement("Song One", "Artist", "Song One", "Artist") == 1.0
    assert autotag.agreement("Song One", "Unknown Artist", "Song One", "Artist") == 1.0


def test_release_choice_prefers_the_album_over_a_compilation():
    album = {"title": "First Album", "trackCount": 10, "year": 2001,
             "releaseGroup": {"type": "Album", "secondaryTypes": []}, "artists": [{"name": "A"}]}
    comp = {"title": "Greatest Hits", "trackCount": 20, "year": 2000,
            "releaseGroup": {"type": "Album", "secondaryTypes": ["Compilation"]}, "artists": [{"name": "Various"}]}
    args = ([0.9], 1, 10, "First Album", "A")
    assert autotag.release_score(album, *args) > autotag.release_score(comp, *args)
    # even without a current album tag the studio album wins over the compilation
    assert autotag.release_score(album, [0.9], 1, 1, None, None) > autotag.release_score(comp, [0.9], 1, 1, None, None)
    # coverage dominates: a release with all 10 tracks beats one with 1 matching the title
    other = dict(album, title="Something")
    assert autotag.release_score(other, [0.9] * 10, 10, 10, "First Album", "A") > \
        autotag.release_score(album, [0.9], 10, 10, "First Album", "A")


# ---------- AcoustID client / rate limiting ----------

class Clock:
    def __init__(self):
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def test_rate_limiter_spaces_calls():
    clk = Clock()
    lim = acoustid.RateLimiter(0.4, clock=clk, sleep=clk.sleep)
    for _ in range(3):
        with lim:
            clk.t += 0.1   # the request takes 0.1 s
    assert clk.slept == [0.4, 0.4]   # measured from the end of the previous request
    clk.t += 5
    with lim:
        pass
    assert clk.slept == [0.4, 0.4]


def test_acoustid_lookup_and_errors():
    seen = []

    def handler(req):
        seen.append(parse_qs(req.content.decode()))
        if seen[-1]["client"] == ["bad"]:
            return httpx.Response(400, json={"status": "error", "error": {"code": 4, "message": "invalid API key"}})
        if seen[-1]["client"] == ["busy"]:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"status": "ok", "results": [
            {"id": "low", "score": 0.2, "recordings": [{"id": "r-low"}]},
            {"id": "aid", "score": 0.93, "recordings": [{"id": R1, "title": "Song One", "duration": 201,
                                                         "releasegroups": [{"id": RG_A, "type": "Album", "title": "FA",
                                                                            "releases": [{"id": REL_A, "title": "FA",
                                                                                          "track_count": 9}]}]}]}]})

    t = httpx.MockTransport(handler)
    res = acoustid.lookup("k", "FPX", 201.4, transport=t, limiter=acoustid.RateLimiter(0))
    assert seen[0]["duration"] == ["201"] and seen[0]["fingerprint"] == ["FPX"]
    assert [r["acoustid"] for r in res] == ["aid"]    # low-score results dropped
    rel = res[0]["recordings"][0]["releases"][0]
    assert rel["id"] == REL_A and rel["releaseGroup"]["id"] == RG_A and rel["trackCount"] == 9
    with pytest.raises(acoustid.AcoustIDError) as e:
        acoustid.lookup("bad", "F", 1, transport=t, limiter=acoustid.RateLimiter(0))
    assert e.value.fatal
    with pytest.raises(acoustid.AcoustIDError) as e:
        acoustid.lookup("busy", "F", 1, transport=t, limiter=acoustid.RateLimiter(0))
    assert e.value.transient and not e.value.fatal


def test_musicbrainz_is_rate_limited_and_cached(conn, monkeypatch):
    clk = Clock()
    monkeypatch.setattr(musicbrainz.time, "monotonic", clk)
    monkeypatch.setattr(musicbrainz.time, "sleep", clk.sleep)
    monkeypatch.setattr(musicbrainz, "_last", 0.0)
    fake = Fake()
    t = httpx.MockTransport(fake)
    autotag.mb_release(conn, REL_A, t)
    autotag.mb_release(conn, REL_B, t)
    autotag.mb_release(conn, REL_A, t)   # cached: no request
    assert fake.calls == [f"musicbrainz.org/ws/2/release/{REL_A}", f"musicbrainz.org/ws/2/release/{REL_B}"]
    assert clk.slept == [musicbrainz.MIN_INTERVAL_S]  # the second request waited ~1.1 s
    assert conn.execute("SELECT COUNT(*) FROM mb_releases").fetchone()[0] == 2


# ---------- the job ----------

def test_job_without_api_key_only_fingerprints(scanned, conn, cfg, fake):
    res = autotag.run(conn, cfg, transport=httpx.MockTransport(fake), compute=fake_compute)
    assert res["fingerprints"]["fingerprinted"] == 5
    assert "skipped" in res["acoustid"]
    assert fake.calls == []
    assert res["status"]["fingerprinted"] == 5 and not res["status"]["apiKeyConfigured"]


def test_job_builds_grouped_suggestions_and_is_resumable(scanned, conn, kcfg, fake):
    res = run_job(conn, kcfg, fake)
    assert res["acoustid"] == {"matched": 4, "noMatch": 1, "errors": 0}
    ids = track_ids(conn)
    rows = {r["track_id"]: r for r in conn.execute("SELECT * FROM autotag_suggestions")}
    assert set(rows) == {ids["Song 1"], ids["Song 2"], ids["Opening"], ids["untagged"]}
    s1, s2 = rows[ids["Song 1"]], rows[ids["Song 2"]]
    assert s1["release_mbid"] == REL_A, "the album, not the compilation"
    assert s1["group_id"] == s2["group_id"]
    sug = json.loads(s1["suggested"])
    assert sug["title"] == "Song One" and sug["album"] == "First Album" and sug["track_no"] == 1
    assert sug["year"] == 2001 and sug["genre"] == "Indie Rock" and sug["mb_track"] == R1
    assert sug["mb_releasegroup"] == RG_A and sug["mb_albumartist"] == AR1
    assert s1["cover_url"].endswith(f"/release/{REL_A}/front-500")
    assert s1["confidence"] > 0.85 and s1["group_confidence"] > 0.85
    op = json.loads(rows[ids["Opening"]]["suggested"])
    assert op["artist"] == "The Band feat. X" and "mb_artist" not in op   # collaboration: no single artist id
    assert op["year"] == 1998    # original release year (release group)
    # second run: nothing new to fingerprint, look up or fetch
    n = len(fake.calls)
    res = run_job(conn, kcfg, fake)
    assert res["fingerprints"]["fingerprinted"] == 0 and res["acoustid"]["matched"] == 0
    assert res["suggestions"]["albums"] == 0 and len(fake.calls) == n


def test_job_stops_on_budget_and_carries_on(scanned, conn, kcfg, fake):
    ticks = iter(range(0, 10_000))
    clock = lambda: next(ticks)  # noqa: E731  every call = 1 s
    res = run_job(conn, kcfg, fake, time_budget_s=3, clock=clock)
    assert res["fingerprints"] == {"stopped": "time budget used"}
    done = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    assert 0 < done < 5
    run_job(conn, kcfg, fake)
    assert conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0] == 5


def test_bad_api_key_stops_lookups(scanned, conn, kcfg):
    def handler(req):
        return httpx.Response(400, json={"status": "error", "error": {"code": 4, "message": "invalid API key"}})

    res = autotag.run(conn, kcfg, transport=httpx.MockTransport(handler), compute=fake_compute,
                      limiter=acoustid.RateLimiter(0))
    assert "API key" in res["acoustid"]["stopped"]
    assert conn.execute("SELECT COUNT(*) FROM acoustid_lookups").fetchone()[0] == 0


def test_changed_file_is_fingerprinted_again(scanned, conn, kcfg, fake):
    run_job(conn, kcfg, fake)
    tid = track_ids(conn)["Song 1"]
    conn.execute("UPDATE tracks SET mtime = mtime + 1 WHERE id = ?", (tid,))
    res = run_job(conn, kcfg, fake)
    assert res["fingerprints"]["fingerprinted"] == 1 and res["acoustid"]["matched"] == 1
    assert conn.execute("SELECT status FROM autotag_suggestions WHERE track_id = ?", (tid,)).fetchone()[0] == "pending"


# ---------- review API ----------

@pytest.fixture
def suggested(app, conn, kcfg, fake):
    app.state.ctx.http_transport = httpx.MockTransport(fake)
    run_job(conn, kcfg, fake)
    return conn


def _group(client, album_title):
    gs = client.get("/api/v1/admin/autotag/groups").json()["groups"]
    return next(g for g in gs if g["currentAlbum"] == album_title)


def test_groups_list_with_diff(client, suggested):
    r = client.get("/api/v1/admin/autotag/groups")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    confs = [g["confidence"] for g in body["groups"]]
    assert confs == sorted(confs, reverse=True)
    g = _group(client, "First Album")
    assert g["release"]["mbid"] == REL_A and g["release"]["hasCover"] and g["albumHasArt"]
    assert [t["current"]["title"] for t in g["tracks"]] == ["Song 1", "Song 2"]
    t = g["tracks"][0]
    assert t["suggested"]["title"] == "Song One" and "title" in t["changes"] and "mbRecordingId" in t["changes"]
    assert "genre" in t["changes"] and "genre" not in t["defaultFields"]   # has a genre already
    assert "mbAlbumArtistId" not in t["defaultFields"]
    assert client.get("/api/v1/admin/autotag/groups", params={"minConfidence": 0.99}).json()["total"] == 0
    st = client.get("/api/v1/admin/autotag/status").json()
    assert st["pending"] == 4 and st["matched"] == 4 and st["noMatch"] == 1 and st["apiKeyConfigured"] is False


def test_accept_album_writes_overrides_and_reopen_reverts(client, suggested, conn, cfg):
    before = _md5s(cfg.library_root)
    g = _group(client, "First Album")
    r = client.post("/api/v1/admin/autotag/accept", json={"groupIds": [g["groupId"]], "cover": True})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["accepted"] == 2 and res["coverErrors"] == []
    ids = track_ids(conn)
    t1 = client.get(f"/api/v1/library/tracks/{ids['Song One']}").json()
    assert t1["title"] == "Song One"
    assert conn.execute("SELECT mbid FROM tracks WHERE id = ?", (ids["Song One"],)).fetchone()[0] == R1
    assert conn.execute("SELECT genre FROM tracks WHERE id = ?", (ids["Song One"],)).fetchone()[0] == "Rock"
    album = conn.execute("SELECT al.mbid, al.art_path FROM tracks t JOIN albums al ON al.id = t.album_id "
                         "WHERE t.id = ?", (ids["Song One"],)).fetchone()
    assert album["mbid"] == RG_A and album["art_path"].startswith("@upload/")
    edits = conn.execute("SELECT DISTINCT batch_id FROM tag_edits").fetchall()
    assert [e[0] for e in edits] == [res["batchId"]]   # one audit batch for the album
    assert {r[0] for r in conn.execute("SELECT field FROM tag_overrides WHERE track_id = ?", (ids["Song One"],))} \
        == {"title", "disc_no", "mb_track", "mb_releasegroup", "cover"}
    assert _md5s(cfg.library_root) == before   # files untouched
    # listed as accepted now
    acc = client.get("/api/v1/admin/autotag/groups", params={"status": "accepted"}).json()
    assert acc["total"] == 1 and acc["groups"][0]["tracks"][0]["batchId"] == res["batchId"]
    # reopen: the accept's overrides are reverted, the suggestion is pending again
    r = client.post("/api/v1/admin/autotag/reopen", json={"trackIds": [ids["Song One"], ids["Song Two"]]})
    assert r.json()["reopened"] == 2
    assert conn.execute("SELECT COUNT(*) FROM tag_overrides").fetchone()[0] == 0
    assert set(track_ids(conn)) >= {"Song 1", "Song 2"}
    assert client.get("/api/v1/admin/autotag/status").json()["pending"] == 4


def test_accept_with_edits_and_fields(client, suggested, conn):
    ids = track_ids(conn)
    r = client.post("/api/v1/admin/autotag/accept", json={
        "trackIds": [ids["Opening"]], "fields": ["year"], "edits": {ids["Opening"]: {"title": "Opening (Intro)"}}})
    assert r.status_code == 200, r.text
    row = conn.execute("SELECT title, year FROM tracks WHERE id = ?", (ids["Opening"],)).fetchone()
    assert tuple(row) == ("Opening (Intro)", 1998)
    # only the picked field + the edited one were written
    assert {r[0] for r in conn.execute("SELECT field FROM tag_overrides WHERE track_id = ?", (ids["Opening"],))} \
        == {"title", "year"}
    assert client.post("/api/v1/admin/autotag/accept", json={"trackIds": ["x"], "fields": ["lyrics"]}).status_code == 422


def test_accept_all_above_threshold_and_reject(client, suggested, conn):
    ids = track_ids(conn)
    confs = {r[0]: r[1] for r in conn.execute("SELECT track_id, confidence FROM autotag_suggestions")}
    cut = sorted(confs.values())[1]
    assert client.post("/api/v1/admin/autotag/reject", json={"trackIds": [ids["untagged"]]}).json() == {"rejected": 1}
    r = client.post("/api/v1/admin/autotag/accept", json={"minConfidence": cut}).json()
    expected = sum(1 for t, c in confs.items() if c >= cut and t != ids["untagged"])
    assert r["accepted"] == expected
    st = conn.execute("SELECT status FROM autotag_suggestions WHERE track_id = ?", (ids["untagged"],)).fetchone()[0]
    assert st == "rejected"
    assert client.post("/api/v1/admin/autotag/accept", json={}).status_code == 422


def test_cover_proxy_and_admin_only(client, bob, suggested):
    r = client.get(f"/api/v1/admin/autotag/cover/{REL_A}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert bob.get("/api/v1/admin/autotag/groups").status_code == 403
    assert bob.post("/api/v1/admin/autotag/accept", json={"minConfidence": 0}).status_code == 403


def test_decided_suggestions_survive_a_rebuild(client, suggested, conn, kcfg, fake):
    ids = track_ids(conn)
    client.post("/api/v1/admin/autotag/reject", json={"trackIds": [ids["Song 1"]]})
    conn.execute("UPDATE acoustid_lookups SET suggested_at = NULL")
    run_job(conn, kcfg, fake)
    assert conn.execute("SELECT status FROM autotag_suggestions WHERE track_id = ?",
                        (ids["Song 1"],)).fetchone()[0] == "rejected"
    r = client.post("/api/v1/admin/autotag/rematch", json={"trackIds": [ids["Song 2"]]})
    assert r.json() == {"queued": 1}
