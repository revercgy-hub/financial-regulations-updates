"""Refresh the live case sources while preserving the audited historical archive.

The Audit Office stopped publishing the dedicated "transferred clues" series after
the 2022 No. 2 announcement.  Re-downloading every legacy PDF on every release is
slow and brittle, so the verified Audit Office records are retained.  MOF, CSRC and
CCDI/NSC records are refreshed from their current official endpoints.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_penalty_cases as cases


LIVE_SOURCES = {"财政部", "证监会", cases.CCDI_SOURCE}


def refresh_mof(session, old_records: list[dict]) -> list[dict]:
    discovered = cases.parse_mof_list(session)
    old_by_url = {item.get("url"): item for item in old_records if item.get("url")}
    refreshed = []
    for index, record in enumerate(discovered, start=1):
        try:
            refreshed.append(cases.parse_mof_case(session, record))
        except Exception as error:
            cached = old_by_url.get(record.get("url"))
            if cached:
                refreshed.append(cached)
                print(f"MOF {index}/{len(discovered)}: kept cached copy ({error})")
            else:
                print(f"MOF {index}/{len(discovered)}: skipped new item ({error})")
        if index % 25 == 0:
            print(f"MOF {index}/{len(discovered)}")
        time.sleep(0.08)
    return refreshed


def require_healthy(source: str, current: list[dict], previous: list[dict], floor: float) -> None:
    old_count = len(previous)
    new_count = len(current)
    minimum = max(1, int(old_count * floor))
    if new_count < minimum:
        raise RuntimeError(
            f"{source} refresh returned only {new_count} records; expected at least {minimum} "
            f"from the previous {old_count}. Existing library was not replaced."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh current official case sources safely.")
    parser.add_argument(
        "--sources",
        default="mof,csrc,ccdi",
        help="comma-separated live sources to refresh: mof,csrc,ccdi",
    )
    parser.add_argument("--ccdi-pages", type=int, default=2)
    parser.add_argument("--ccdi-detail-limit", type=int, default=40)
    args = parser.parse_args()
    requested = {item.strip().lower() for item in args.sources.split(",") if item.strip()}
    unknown = requested - {"mof", "csrc", "ccdi"}
    if unknown or not requested:
        raise SystemExit(f"Invalid --sources value: {args.sources}")

    existing = cases.load_existing_cases(cases.ROOT)
    by_source = {
        source: [record for record in existing if record.get("source") == source]
        for source in {record.get("source") for record in existing}
    }
    audit_records = by_source.get("审计署", [])
    if not audit_records:
        raise RuntimeError("The verified Audit Office archive is missing; refusing to rebuild.")

    session = cases.make_session()
    mof_records = by_source.get("财政部", [])
    if "mof" in requested:
        print("Refreshing Ministry of Finance cases...")
        mof_records = refresh_mof(session, mof_records)
        require_healthy("财政部", mof_records, by_source.get("财政部", []), 0.90)

    csrc_records = by_source.get("证监会", [])
    if "csrc" in requested:
        print("Refreshing CSRC cases...")
        csrc_records = cases.parse_csrc_list(session)
        require_healthy("证监会", csrc_records, by_source.get("证监会", []), 0.95)

    ccdi_records = by_source.get(cases.CCDI_SOURCE, [])
    if "ccdi" in requested:
        print("Refreshing CCDI/NSC notices...")
        ccdi_records = cases.parse_ccdi_cases(
            limit=None,
            pages_per_channel=args.ccdi_pages,
            detail_limit=args.ccdi_detail_limit,
        )
        require_healthy(cases.CCDI_SOURCE, ccdi_records, by_source.get(cases.CCDI_SOURCE, []), 0.90)

    combined = []
    seen = set()
    for raw in mof_records + csrc_records + audit_records + ccdi_records:
        key = raw.get("url") or raw.get("sha256")
        if not key or key in seen:
            continue
        seen.add(key)
        combined.append(cases.enrich_case(raw, len(combined) + 1))
    combined.sort(
        key=lambda item: (item.get("publish_date", ""), item.get("source", ""), item.get("file_no", "")),
        reverse=True,
    )

    counts = Counter(item["source"] for item in combined)
    if set(counts) != {"财政部", "证监会", "审计署", cases.CCDI_SOURCE}:
        raise RuntimeError(f"Unexpected source set: {sorted(counts)}")

    cases.write_kb(combined, cases.ROOT)
    cases.write_site(combined, cases.ROOT, cases.SITE)
    print(f"Done. Refreshed {len(combined)} cases: {dict(counts)}")


if __name__ == "__main__":
    main()
