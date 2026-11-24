"""The whole migration chain, from every schema version we ever shipped (tests/fixtures/schema_vN.sql) up to
db.SCHEMA_VERSION. The per-step tests (test_migration_vN.py) check what each step adds; these check that the
steps together land on exactly the schema a fresh install gets, that no row is lost on the way, and that a
failed step leaves the database as it was. v5 -> v6 and v6 -> v7 each combine several features' changes.

Adding schema v9: copy the current schema.sql to tests/fixtures/schema_v8.sql *before* changing it;
test_every_old_version_has_a_fixture fails until that fixture exists."""
from __future__ import annotations

import collections
import re
import sqlite3
from pathlib import Path

import pytest

from nasmusic import db
from nasmusic.db import migrations

FIXTURES = Path(__file__).parent / "fixtures"
OLD_VERSIONS = list(range(1, db.SCHEMA_VERSION))
# Versions whose fixture holds every table we care about filling generically (v1-v4 are covered with real
# data by test_v1_rows_survive / test_v4_rows_survive below; their steps move columns between tables).
FILLABLE = [v for v in OLD_VERSIONS if v >= 5]
STEPS = {5: "migrate_v5_to_v6", 6: "migrate_v6_to_v7", 7: "migrate_v7_to_v8", 8: "migrate_v8_to_v9", 9: "migrate_v9_to_v10"}


# ---------- helpers ----------

def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _shape(conn: sqlite3.Connection) -> dict:
    """Tables (columns: type, not null, default, pk; foreign keys), indexes (table, columns, unique), triggers
    and views. Column order is ignored: ALTER TABLE ADD COLUMN appends where a fresh CREATE may not."""
    out: dict = {}
    for t in _tables(conn):
        cols = {r[1]: (r[2].upper(), r[3], r[4], r[5]) for r in conn.execute(f"PRAGMA table_info('{t}')")}
        fks = sorted(tuple(r[2:]) for r in conn.execute(f"PRAGMA foreign_key_list('{t}')"))
        out[f"table {t}"] = (cols, fks)
    for name, tbl in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"):
        cols = [r[2] for r in conn.execute(f"PRAGMA index_xinfo('{name}')") if r[5]]
        unique = next(r[2] for r in conn.execute(f"PRAGMA index_list('{tbl}')") if r[1] == name)
        where = re.search(r"\bWHERE\b(.*)$", conn.execute("SELECT sql FROM sqlite_master WHERE name = ?",
                                                           (name,)).fetchone()[0], re.S | re.I)
        out[f"index {name}"] = (tbl, cols, unique, " ".join(where.group(1).split()) if where else None)
    for kind, name in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')"):
        out[f"{kind} {name}"] = True
    return out


def _make(path: Path, version: int) -> sqlite3.Connection:
    conn = db.connect(path)
    conn.executescript((FIXTURES / f"schema_v{version}.sql").read_text())
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    return conn


def _fill(conn: sqlite3.Connection) -> None:
    """One row in every table: each column gets a value of its declared type, or the first value its
    `col IN ('a', ...)` CHECK allows. Foreign keys are off while filling, so references needn't resolve."""
    conn.execute("PRAGMA foreign_keys = OFF")
    virtual = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                          "AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
    for t in _tables(conn):
        if t == "schema_version" or any(t.startswith(f"{v}_") for v in virtual):
            continue  # FTS shadow tables are filled through their virtual table
        info = list(conn.execute(f"PRAGMA table_info('{t}')"))
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).fetchone()[0]
        enums = {m.group(1): re.findall(r"'([^']*)'", m.group(2))[0]
                 for m in re.finditer(r"(\w+)\s+IN\s*\(([^)]*)\)", sql) if "'" in m.group(2)}

        def value(col):
            name, decl = col[1], (col[2] or "").upper()
            if name in enums:
                return enums[name]
            if "INT" in decl:
                return 1
            if any(k in decl for k in ("REAL", "FLOA", "DOUB")):
                return 1.5
            if "BLOB" in decl:
                return b"\x00"
            return f"{t}.{name}"

        cols = ", ".join(f'"{c[1]}"' for c in info)
        conn.execute(f'INSERT INTO "{t}" ({cols}) VALUES ({", ".join("?" * len(info))})', [value(c) for c in info])
    conn.execute("PRAGMA foreign_keys = ON")


def _rows(conn: sqlite3.Connection) -> dict[str, tuple[list[str], collections.Counter]]:
    out = {}
    for t in _tables(conn):
        if t == "schema_version":
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
        out[t] = (cols, collections.Counter(tuple(r) for r in conn.execute(f'SELECT * FROM "{t}"')))
    return out


def _assert_rows_kept(before: dict, conn: sqlite3.Connection, changed: dict[str, set[str]] | None = None) -> None:
    """Every table that existed before still has every row, with the same values in every column it kept
    (minus `changed`: columns a migration rewrites on purpose)."""
    changed = changed or {}
    after = _rows(conn)
    for t, (cols, rows) in before.items():
        assert t in after, f"table {t} was dropped"
        keep = [c for c in cols if c in after[t][0] and c not in changed.get(t, set())]
        want = collections.Counter(tuple(r[cols.index(c)] for c in keep) for r in rows.elements())
        idx = [after[t][0].index(c) for c in keep]
        got = collections.Counter(tuple(r[i] for i in idx) for r in after[t][1].elements())
        assert got == want, f"rows of {t} changed in {keep}"


@pytest.fixture(scope="module")
def fresh_shape(tmp_path_factory):
    conn = db.connect(tmp_path_factory.mktemp("fresh") / "nasmusic.db")
    db.init(conn)
    yield _shape(conn)
    conn.close()


# ---------- bookkeeping ----------

def test_every_old_version_has_a_fixture():
    missing = [v for v in OLD_VERSIONS if not (FIXTURES / f"schema_v{v}.sql").exists()]
    assert not missing, (f"no tests/fixtures/schema_v{missing}.sql: copy schema.sql there before bumping "
                         f"SCHEMA_VERSION, so the upgrade from it stays tested")


def test_schema_sql_header_names_the_current_version():
    assert db.schema_sql().startswith(f"-- nas-music shared schema v{db.SCHEMA_VERSION} ")


def test_each_fixture_is_the_version_it_claims():
    for v in OLD_VERSIONS:
        head = (FIXTURES / f"schema_v{v}.sql").read_text().splitlines()[0]
        if "schema v" in head:
            assert f"schema v{v} " in head, f"schema_v{v}.sql starts with {head!r}"


def test_fresh_install_is_current_version(tmp_path):
    conn = db.connect(tmp_path / "nasmusic.db")
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


# ---------- the chain lands on the fresh schema ----------

@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_migrated_schema_matches_fresh_install(tmp_path, version, fresh_shape):
    path = tmp_path / "nasmusic.db"
    _make(path, version).close()
    conn = db.connect(path)
    db.init(conn, admin_password_hash="!")
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    got = _shape(conn)
    diff = sorted(k for k in set(fresh_shape) | set(got) if fresh_shape.get(k) != got.get(k))
    detail = "\n".join(f"  {k}:\n    fresh:    {fresh_shape.get(k)}\n    migrated: {got.get(k)}" for k in diff[:10])
    assert not diff, f"v{version} -> v{db.SCHEMA_VERSION} differs from a fresh install in {len(diff)} places:\n{detail}"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list(tmp_path.glob("nasmusic.db.*-backup-*"))) == 1, "one backup per start, however many steps"


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_chain_is_idempotent(tmp_path, version):
    path = tmp_path / "nasmusic.db"
    _make(path, version).close()
    conn = db.connect(path)
    db.init(conn, admin_password_hash="!")
    shape = _shape(conn)
    for _ in range(2):
        db.init(conn, admin_password_hash="!")
    assert _shape(conn) == shape
    assert len(list(tmp_path.glob("nasmusic.db.*-backup-*"))) == 1


# ---------- no data is lost ----------

@pytest.mark.parametrize("version", FILLABLE)
def test_every_row_survives(tmp_path, version):
    path = tmp_path / "nasmusic.db"
    conn = _make(path, version)
    _fill(conn)
    before = _rows(conn)
    assert all(rows for _, rows in before.values()), "the generic filler skipped a table"
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    _assert_rows_kept(before, conn)


def test_v1_rows_survive(tmp_path):
    """v1 -> v2 moves per-user columns (rating, starred, blocked) out of tracks (test_migration.py checks where
    they land); v3 -> v4 zeroes tracks.mtime so the next scan re-reads tags. Everything else is kept."""
    from tests.test_migration import make_v1

    path = tmp_path / "nasmusic.db"
    conn = make_v1(path)
    before = _rows(conn)
    conn.close()
    conn = db.connect(path)
    db.init(conn, admin_username="owner", admin_password_hash="!")
    _assert_rows_kept(before, conn, changed={"tracks": {"mtime"}})
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE mtime != 0").fetchone()[0] == 0


def test_v4_rows_survive(tmp_path):
    """v4 -> v5 renumbers playlist_tracks.position (fractional positions, same order)."""
    from tests.test_migration_v5 import make_v4

    path = tmp_path / "nasmusic.db"
    conn = make_v4(path)
    order = [r[0] for r in conn.execute("SELECT track_id FROM playlist_tracks ORDER BY position")]
    before = _rows(conn)
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    _assert_rows_kept(before, conn, changed={"playlist_tracks": {"position"}})
    assert [r[0] for r in conn.execute("SELECT track_id FROM playlist_tracks ORDER BY position")] == order


# ---------- a failed step changes nothing ----------

@pytest.mark.parametrize("version", sorted(STEPS))
def test_failed_step_rolls_back(tmp_path, monkeypatch, version):
    path = tmp_path / "nasmusic.db"
    conn = _make(path, version)
    _fill(conn)
    before_rows, before_shape = _rows(conn), _shape(conn)

    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(migrations, "_create_indexes", boom)  # every step's last act before COMMIT
    with pytest.raises(RuntimeError, match="disk on fire"):
        getattr(migrations, STEPS[version])(conn, db.schema_sql(), make_backup=False)
    assert migrations.current_version(conn) == version
    assert _shape(conn) == before_shape
    assert _rows(conn) == before_rows
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign keys left off after a failure"
    assert not conn.in_transaction

    monkeypatch.undo()
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    _assert_rows_kept(before_rows, conn)


def test_failure_mid_chain_keeps_finished_steps_and_resumes(tmp_path, monkeypatch):
    """v5 started up with a failing v7 -> v8: v5 -> v6 -> v7 stay committed, the next start finishes the job."""
    path = tmp_path / "nasmusic.db"
    conn = _make(path, 5)
    _fill(conn)
    before = _rows(conn)
    conn.close()

    def boom(*_a, **_k):
        raise RuntimeError("power cut")

    monkeypatch.setattr(migrations, "migrate_v7_to_v8", boom)
    conn = db.connect(path)
    with pytest.raises(RuntimeError, match="power cut"):
        db.init(conn)
    assert migrations.current_version(conn) == 7
    conn.close()

    monkeypatch.undo()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    _assert_rows_kept(before, conn)
    backups = sorted(p.name.split(".")[2].split("-")[0] for p in tmp_path.glob("nasmusic.db.*-backup-*"))
    assert backups == ["v5", "v7"], "each start backs up the version it starts from"
