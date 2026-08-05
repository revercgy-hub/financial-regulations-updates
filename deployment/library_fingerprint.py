from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = {
    "cases": (
        ROOT / "penalty_cases_kb" / "index.jsonl",
        ("source", "url", "sha256", "title", "publish_date", "file_no"),
    ),
    "regulations": (
        ROOT / "iweicha_ffs_kb" / "index.jsonl",
        ("type", "doc_id", "sha256", "title", "year", "file_no", "state"),
    ),
    "accounting": (
        ROOT / "maodocs_kb" / "index.jsonl",
        ("url", "sha256", "title", "lastmod"),
    ),
}


def fingerprint(path: Path, fields: tuple[str, ...]) -> dict[str, object]:
    if not path.is_file():
        return {"count": 0, "sha256": "missing"}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows.append([str(item.get(field, "")) for field in fields])
    rows.sort()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"count": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}


def snapshot() -> dict[str, dict[str, object]]:
    return {name: fingerprint(path, fields) for name, (path, fields) in LIBRARIES.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fingerprint the three server-side source libraries.")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    if not args.compare:
        print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
        return

    before = json.loads(Path(args.compare[0]).read_text(encoding="utf-8-sig"))
    after = json.loads(Path(args.compare[1]).read_text(encoding="utf-8-sig"))
    changes = {name: before.get(name) != after.get(name) for name in LIBRARIES}
    changes["changed"] = any(changes.values())
    lines = [
        *(f"{name}_changed={str(changes[name]).lower()}" for name in LIBRARIES),
        f"changed={str(changes['changed']).lower()}",
    ]
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines) + "\n")
    print(json.dumps({"before": before, "after": after, "changes": changes}, indent=2))


if __name__ == "__main__":
    main()
