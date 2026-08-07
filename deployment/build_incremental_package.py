"""Build and validate an entry-level delta between two knowledge packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "revercgy-hub/financial-regulations-updates"
BUFFER_SIZE = 1024 * 1024


def safe_name(raw: str) -> str:
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"Unsafe ZIP entry: {raw!r}")
    return path.as_posix()


def file_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = safe_name(info.filename)
        if name in entries:
            raise RuntimeError(f"Duplicate ZIP entry: {name}")
        entries[name] = info
    return entries


def hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(BUFFER_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hash_stream(stream)


def hash_entries(
    archive: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo]
) -> dict[str, str]:
    result = {}
    for name in sorted(entries):
        with archive.open(entries[name]) as stream:
            result[name] = hash_stream(stream)
    return result


def read_package_manifest(archive: zipfile.ZipFile, label: str) -> dict:
    try:
        raw = archive.read("package.json")
    except KeyError as error:
        raise RuntimeError(f"{label} package has no package.json") from error
    if len(raw) > 1024 * 1024:
        raise RuntimeError(f"{label} package.json is too large")
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("scope") != "regulations" or manifest.get("schema") != 1:
        raise RuntimeError(f"{label} package manifest is invalid")
    return manifest


def add_payload_entry(
    source: zipfile.ZipFile,
    source_info: zipfile.ZipInfo,
    destination: zipfile.ZipFile,
    name: str,
) -> None:
    output_info = zipfile.ZipInfo(f"payload/{name}", date_time=source_info.date_time)
    output_info.compress_type = zipfile.ZIP_DEFLATED
    output_info.external_attr = source_info.external_attr
    with source.open(source_info) as input_stream, destination.open(
        output_info, "w", force_zip64=True
    ) as output_stream:
        shutil.copyfileobj(input_stream, output_stream, BUFFER_SIZE)


def validate_delta(
    delta_path: Path,
    base_entries: dict[str, zipfile.ZipInfo],
    base_hashes: dict[str, str],
    target_entries: dict[str, zipfile.ZipInfo],
    target_hashes: dict[str, str],
) -> None:
    with zipfile.ZipFile(delta_path) as delta:
        delta_entries = file_entries(delta)
        metadata = json.loads(delta.read("delta.json").decode("utf-8"))
        listed = {item["path"]: item for item in metadata["files"]}
        expected_payload = {f"payload/{name}" for name in listed}
        actual_payload = set(delta_entries) - {"delta.json"}
        if actual_payload != expected_payload:
            raise RuntimeError("Delta payload does not match delta.json")
        if set(metadata["delete"]) != set(base_entries) - set(target_entries):
            raise RuntimeError("Delta deletion list is incomplete")

        for name, info in target_entries.items():
            if name in listed:
                payload_info = delta_entries[f"payload/{name}"]
                with delta.open(payload_info) as stream:
                    digest = hash_stream(stream)
                if digest != target_hashes[name]:
                    raise RuntimeError(f"Delta payload hash mismatch: {name}")
                if listed[name]["sha256"] != digest or listed[name]["size"] != info.file_size:
                    raise RuntimeError(f"Delta metadata mismatch: {name}")
            elif name not in base_entries or base_hashes[name] != target_hashes[name]:
                raise RuntimeError(f"Delta cannot reconstruct target entry: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-package", required=True, type=Path)
    parser.add_argument("--target-package", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    public_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    target_version = str(public_manifest["version"])
    if not re.fullmatch(r"[0-9]{8}\.[0-9]+", target_version):
        raise RuntimeError("Target version is invalid")

    with zipfile.ZipFile(args.base_package) as base, zipfile.ZipFile(
        args.target_package
    ) as target:
        base_entries = file_entries(base)
        target_entries = file_entries(target)
        base_manifest = read_package_manifest(base, "Base")
        target_manifest = read_package_manifest(target, "Target")
        base_version = str(base_manifest["version"])
        if target_version != str(target_manifest["version"]):
            raise RuntimeError("Target package and public manifest versions differ")
        if int(target_manifest["version_code"]) != int(public_manifest["version_code"]):
            raise RuntimeError("Target package and public manifest version codes differ")
        if int(target_manifest["version_code"]) <= int(base_manifest["version_code"]):
            raise RuntimeError("Target version must be newer than base version")

        base_hashes = hash_entries(base, base_entries)
        target_hashes = hash_entries(target, target_entries)
        changed = sorted(
            name
            for name in target_entries
            if name not in base_hashes or base_hashes[name] != target_hashes[name]
        )
        deleted = sorted(set(base_entries) - set(target_entries))
        if "package.json" not in changed:
            raise RuntimeError("Every delta must replace package.json")

        metadata = {
            "schema": 1,
            "scope": "regulations",
            "base_version": base_version,
            "base_version_code": int(base_manifest["version_code"]),
            "target_version": target_version,
            "target_version_code": int(target_manifest["version_code"]),
            "files": [
                {
                    "path": name,
                    "size": target_entries[name].file_size,
                    "sha256": target_hashes[name],
                }
                for name in changed
            ],
            "delete": deleted,
        }
        asset_name = f"knowledge-delta-{base_version}-to-{target_version}.zip"
        delta_path = args.target_package.parent / asset_name
        with zipfile.ZipFile(
            delta_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as delta:
            delta.writestr(
                "delta.json",
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n",
            )
            for name in changed:
                add_payload_entry(target, target_entries[name], delta, name)

        validate_delta(
            delta_path, base_entries, base_hashes, target_entries, target_hashes
        )

    tag = f"knowledge-{target_version}"
    public_manifest["delta"] = {
        "schema": 1,
        "base_version": base_version,
        "base_version_code": int(base_manifest["version_code"]),
        "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}",
        "size": delta_path.stat().st_size,
        "sha256": hash_file(delta_path),
        "changed_files": len(changed),
        "deleted_files": len(deleted),
    }
    args.manifest.write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    ratio = delta_path.stat().st_size / args.target_package.stat().st_size * 100
    print(
        f"Built {delta_path.name}: {delta_path.stat().st_size / 1024 / 1024:.1f} MiB "
        f"({ratio:.1f}% of full package), {len(changed)} changed + {len(deleted)} deleted files"
    )


if __name__ == "__main__":
    main()
