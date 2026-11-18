"""AI engine maintenance CLI.

    python -m nasmusic.ai selftest        [--data-dir D] [--model-version V]
    python -m nasmusic.ai download-models  --data-dir D  [--model-version V]
    python -m nasmusic.ai benchmark        --db F --library L --data-dir D [-n 20] [--workers 2]
    python -m nasmusic.ai analyse          --db F --library L --data-dir D [--limit N]
    python -m nasmusic.ai nightly          --db F --library L --data-dir D
    python -m nasmusic.ai discovery        --db F --library L --data-dir D
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path


def _cpu_flags() -> set[str]:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags"):
                return set(line.split(":", 1)[1].split())
    except OSError:
        pass
    return set()


SELFTEST_CHILD = """
import sys, time
from pathlib import Path
from nasmusic.ai.extractors import make_extractor
t = time.monotonic(); ex = make_extractor(sys.argv[1], Path(sys.argv[2])); load = time.monotonic() - t
t = time.monotonic(); f = ex.extract_file(Path(sys.argv[3]), 30.0); run = time.monotonic() - t
print(f"ok dims={f.embedding.size} bpm={f.bpm} key={f.key} energy={f.energy} load={load:.1f}s extract={run:.1f}s")
"""


def selftest(data_dir: Path, version: str) -> int:
    from .extractors import essentia_available, ffmpeg_path

    flags = _cpu_flags()
    print(f"cpu: avx={'avx' in flags} avx2={'avx2' in flags} fma={'fma' in flags}")
    print(f"ffmpeg: {ffmpeg_path()}")
    print(f"essentia importable: {essentia_available()}")
    if version != "basic-1":
        from . import models
        models.ensure_models(data_dir, version)
        print(f"models verified in {models.model_dir(data_dir, version)}")
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "tone.flac"
        subprocess.run([ffmpeg_path(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
                        "-ar", "44100", str(clip)], check=True)
        # run in a child so an illegal instruction (SIGILL) is reported instead of killing us
        p = subprocess.run([sys.executable, "-c", SELFTEST_CHILD, version, str(data_dir), str(clip)],
                           capture_output=True, text=True, check=False)
    if p.returncode != 0:
        sig = -p.returncode if p.returncode < 0 else None
        print(f"FAILED (exit {p.returncode}{', SIGILL: CPU lacks an instruction set' if sig == 4 else ''})")
        print(p.stderr[-2000:])
        return 1
    print(p.stdout.strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nasmusic.ai")
    ap.add_argument("command", choices=["selftest", "download-models", "benchmark", "analyse", "nightly",
                                        "discovery"])
    ap.add_argument("--db", type=Path)
    ap.add_argument("--library", type=Path)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--model-version", default="effnet-discogs-1")
    ap.add_argument("--timezone", default="Europe/London")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.command == "selftest":
        return selftest(a.data_dir, a.model_version)
    if a.command == "download-models":
        from . import models
        print(models.ensure_models(a.data_dir, a.model_version))
        return 0
    if not a.db or not a.library:
        ap.error("--db and --library are required for this command")
    from nasmusic import db

    from . import analyser, api
    from .api import AIConfig
    conn = db.connect(a.db)
    db.init(conn)
    cfg = AIConfig(library_root=a.library, data_dir=a.data_dir, model_version=a.model_version, timezone=a.timezone)
    if a.command == "benchmark":
        print(json.dumps(analyser.benchmark(conn, cfg, a.n, workers=a.workers), indent=2))
    elif a.command == "analyse":
        print(api.analyse_pending(conn, cfg, a.limit))
    elif a.command == "nightly":
        print(api.run_nightly(conn, cfg))
    elif a.command == "discovery":
        print(api.run_weekly_discovery(conn, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
