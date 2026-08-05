from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 40_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def unified_root() -> Path:
    candidates = [item.parent.parent for item in ROOT.glob("*/data/manifest.json")]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one unified source tree, found {len(candidates)}")
    return candidates[0]


def selected_files() -> list[Path]:
    source = unified_root()
    required = [
        source / "index.html",
        source / "assets" / "search-index.js",
        source / "assets" / "site.css",
        source / "data" / "manifest.json",
    ]
    files = list(required)
    files.extend(path for path in (source / "docs").rglob("*") if path.is_file())
    files.extend(path for path in (source / "data" / "markdown").rglob("*") if path.is_file())

    for directory, fixed, recursive in (
        (
            ROOT / "penalty_cases_kb",
            ("index.jsonl", "manifest.json"),
            ("markdown", "raw_html"),
        ),
        (
            ROOT / "iweicha_ffs_kb",
            ("index.jsonl", "manifest.json", "discovered.jsonl"),
            ("markdown",),
        ),
        (
            ROOT / "maodocs_kb",
            ("index.jsonl", "manifest.json", "sitemap.xml"),
            ("markdown",),
        ),
    ):
        files.extend(directory / name for name in fixed if (directory / name).is_file())
        for name in recursive:
            base = directory / name
            if base.is_dir():
                files.extend(path for path in base.rglob("*") if path.is_file())

    for name in ("audit_sources", "ccdi_sources"):
        directory = ROOT / name
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file())

    unique = sorted({path.resolve() for path in files})
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing state inputs: " + ", ".join(missing))
    return unique


def pack(destination: Path, version: str) -> None:
    files = selected_files()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True
    ) as archive:
        state = {
            "schema": 1,
            "version": version,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": len(files),
        }
        archive.writestr("automation-state.json", json.dumps(state, ensure_ascii=False, indent=2))
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(json.dumps({**state, "archive": str(destination), "bytes": destination.stat().st_size}))


def unpack(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise RuntimeError(f"State archive has too many entries: {len(entries)}")
        total = sum(item.file_size for item in entries)
        if total > MAX_EXPANDED_BYTES:
            raise RuntimeError(f"State archive expands beyond safety limit: {total}")
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe state path: {item.filename}")
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)
    marker = destination / "automation-state.json"
    if not marker.is_file():
        raise RuntimeError("State archive is missing automation-state.json")
    print(marker.read_text(encoding="utf-8"))
    marker.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack or safely unpack GitHub update state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--output", type=Path, required=True)
    pack_parser.add_argument("--version", required=True)
    unpack_parser = subparsers.add_parser("unpack")
    unpack_parser.add_argument("--input", type=Path, required=True)
    unpack_parser.add_argument("--destination", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == "pack":
        pack(args.output.resolve(), args.version)
    else:
        unpack(args.input.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()
