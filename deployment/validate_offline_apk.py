from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path


MANIFEST_ASSET = "assets/offline-manifest.json"
PACKAGE_ASSET = "assets/knowledge-package.zip"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the manifest and complete knowledge package embedded in an offline APK."
    )
    parser.add_argument("apk", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()

    with zipfile.ZipFile(args.apk) as apk:
        bad_entry = apk.testzip()
        if bad_entry:
            raise SystemExit(f"APK CRC check failed at {bad_entry}")
        names = set(apk.namelist())
        missing = {MANIFEST_ASSET, PACKAGE_ASSET} - names
        if missing:
            raise SystemExit(f"Offline APK is missing assets: {sorted(missing)}")
        manifest = json.loads(apk.read(MANIFEST_ASSET).decode("utf-8"))
        if args.expected_version and manifest.get("version") != args.expected_version:
            raise SystemExit(
                f"Expected knowledge {args.expected_version}, found {manifest.get('version')}"
            )

        digest = hashlib.sha256()
        total = 0
        with tempfile.TemporaryFile() as package_file, apk.open(PACKAGE_ASSET) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                package_file.write(chunk)
                total += len(chunk)
            if total != manifest["package_size"]:
                raise SystemExit(
                    f"Embedded package size {total} != manifest {manifest['package_size']}"
                )
            actual_hash = digest.hexdigest()
            if actual_hash != manifest["sha256"]:
                raise SystemExit("Embedded package SHA-256 does not match the manifest")

            package_file.seek(0)
            with zipfile.ZipFile(package_file) as package:
                bad_entry = package.testzip()
                if bad_entry:
                    raise SystemExit(f"Knowledge package CRC check failed at {bad_entry}")
                required = {"package.json", "index.html", "cases/index.html"}
                missing = required - set(package.namelist())
                if missing:
                    raise SystemExit(f"Knowledge package is missing: {sorted(missing)}")
                package_manifest = json.loads(package.read("package.json").decode("utf-8"))

        for key in (
            "version",
            "version_code",
            "documents",
            "regulation_documents",
            "accounting_documents",
            "fiscal_documents",
            "case_documents",
        ):
            if package_manifest.get(key) != manifest.get(key):
                raise SystemExit(f"Embedded package {key} does not match the manifest")

    print(
        json.dumps(
            {
                "apk": str(args.apk),
                "knowledge_version": manifest["version"],
                "package_bytes": total,
                "package_sha256": actual_hash,
                "regulations": manifest["regulation_documents"],
                "accounting": manifest["accounting_documents"],
                "fiscal": manifest["fiscal_documents"],
                "cases": manifest["case_documents"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
