from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REPOSITORY = "revercgy-hub/financial-regulations-updates"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add the versioned offline APK metadata to a knowledge manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--app-version", required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = manifest["version"]
    expected_name = f"FinReg-KnowledgeBase-Offline-KB{version}-v{args.app_version}.apk"
    if args.apk.name != expected_name:
        raise SystemExit(f"Offline APK must be named {expected_name}, got {args.apk.name}")

    manifest.update(
        {
            "offline_app_version": f"{args.app_version}-offline",
            "offline_knowledge_version": version,
            "offline_app_download_url": (
                f"https://github.com/{REPOSITORY}/releases/download/"
                f"knowledge-{version}/{args.apk.name}"
            ),
            "offline_app_size": args.apk.stat().st_size,
            "offline_app_sha256": sha256(args.apk),
        }
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "version": version,
                "apk": args.apk.name,
                "bytes": args.apk.stat().st_size,
                "sha256": manifest["offline_app_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
