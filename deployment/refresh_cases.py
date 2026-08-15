"""Refresh the live case sources while preserving the audited historical archive.

The Audit Office stopped publishing the dedicated "transferred clues" series after
the 2022 No. 2 announcement.  Re-downloading every legacy PDF on every release is
slow and brittle, so the verified Audit Office records are retained.  MOF, CSRC and
CCDI/NSC records are refreshed from their current official endpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_penalty_cases as cases


LIVE_SOURCES = {"财政部", "证监会", cases.CCDI_SOURCE}


def stable_case_id(record: dict) -> str:
    identity = "\n".join(
        str(record.get(key, ""))
        for key in ("source", "url", "title", "publish_date", "file_no", "sha256")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{record['source']}-{digest}"


def refresh_mof(session, old_records: list[dict]) -> list[dict]:
    discovered = cases.parse_mof_list(session)
    old_hidden = [item for item in old_records if item.get("case_topic") == "隐性债务"]
    old_regular = [item for item in old_records if item.get("case_topic") != "隐性债务"]
    old_by_url = {item.get("url"): item for item in old_regular if item.get("url")}
    refreshed = []
    reused = 0
    fetched = 0
    for index, record in enumerate(discovered, start=1):
        cached = old_by_url.get(record.get("url"))
        unchanged = cached and cached.get("body") and all(
            cases.clean_text(str(cached.get(key, "")))
            == cases.clean_text(str(record.get(key, "")))
            for key in ("title", "file_no", "publish_date")
        )
        if unchanged:
            refreshed.append(cached)
            reused += 1
            continue
        try:
            refreshed.append(cases.parse_mof_case(session, record))
            fetched += 1
        except Exception as error:
            if cached:
                refreshed.append(cached)
                print(f"MOF {index}/{len(discovered)}: kept cached copy ({error})")
            else:
                print(f"MOF {index}/{len(discovered)}: skipped new item ({error})")
        if index % 25 == 0:
            print(f"MOF {index}/{len(discovered)}")
        time.sleep(0.08)
    try:
        hidden = cases.parse_mof_hidden_debt_cases(session)
        print(f"MOF hidden-debt refresh: fetched {len(hidden)} individual cases")
    except Exception as error:
        if not old_hidden:
            raise
        hidden = old_hidden
        print(f"MOF hidden-debt refresh: retained {len(hidden)} cached cases ({error})")
    print(f"MOF incremental refresh: reused {reused}, fetched {fetched}")
    return refreshed + hidden


def refresh_csrc(session, old_records: list[dict], recent_limit: int) -> list[dict]:
    if recent_limit <= 0:
        refreshed = cases.parse_csrc_list(session)
        print(f"CSRC full verification: fetched {len(refreshed)} records")
        return refreshed
    recent = cases.parse_csrc_list(session, limit=recent_limit)
    recent_urls = {record.get("url") for record in recent if record.get("url")}
    historical = [
        record for record in old_records if record.get("url") not in recent_urls
    ]
    print(
        f"CSRC incremental refresh: checked {len(recent)} recent records, "
        f"reused {len(historical)} historical records"
    )
    return recent + historical


def refresh_ccdi(
    old_records: list[dict], pages_per_channel: int, detail_limit: int
) -> list[dict]:
    discovered = cases.parse_ccdi_cases(
        limit=None,
        pages_per_channel=pages_per_channel,
        detail_limit=0,
    )
    old_by_url = {
        record.get("url"): record for record in old_records if record.get("url")
    }
    reused = 0
    fetched = 0
    for record in discovered:
        previous = old_by_url.get(record.get("url"))
        if previous:
            record["body"] = previous.get("body", record.get("body", ""))
            record["raw_html"] = previous.get("raw_html", record.get("raw_html", ""))
            reused += 1
            continue
        if fetched >= detail_limit:
            continue
        try:
            source = cases.request_ccdi_text(record["url"])
            body = cases.ccdi_article_text(source)
            if body:
                record["body"] = body
                record["raw_html"] = source
            fetched += 1
        except Exception as error:
            print(f"CCDI new detail fetch failed: {record['url']} ({error})")
        time.sleep(1.0)
    print(f"CCDI incremental refresh: reused {reused}, fetched {fetched} new details")
    return discovered


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
        help="comma-separated sources to refresh: mof,csrc,ccdi,audit",
    )
    parser.add_argument("--ccdi-pages", type=int, default=2)
    parser.add_argument("--ccdi-detail-limit", type=int, default=40)
    parser.add_argument(
        "--csrc-recent",
        type=int,
        default=250,
        help="number of newest CSRC records to verify; use 0 for a full verification",
    )
    args = parser.parse_args()
    requested = {item.strip().lower() for item in args.sources.split(",") if item.strip()}
    unknown = requested - {"mof", "csrc", "ccdi", "audit"}
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
    if "audit" in requested:
        print("Refreshing National Audit Office cases...")
        refreshed_audit = cases.parse_audit_cases(session)
        require_healthy("审计署", refreshed_audit, audit_records, 0.99)
        audit_records = refreshed_audit
    mof_records = by_source.get("财政部", [])
    if "mof" in requested:
        print("Refreshing Ministry of Finance cases...")
        mof_records = refresh_mof(session, mof_records)
        require_healthy("财政部", mof_records, by_source.get("财政部", []), 0.90)

    csrc_records = by_source.get("证监会", [])
    if "csrc" in requested:
        print("Refreshing CSRC cases...")
        csrc_records = refresh_csrc(session, csrc_records, args.csrc_recent)
        require_healthy("证监会", csrc_records, by_source.get("证监会", []), 0.95)

    ccdi_records = by_source.get(cases.CCDI_SOURCE, [])
    if "ccdi" in requested:
        print("Refreshing CCDI/NSC notices...")
        ccdi_records = refresh_ccdi(
            ccdi_records,
            pages_per_channel=args.ccdi_pages,
            detail_limit=args.ccdi_detail_limit,
        )
        require_healthy(cases.CCDI_SOURCE, ccdi_records, by_source.get(cases.CCDI_SOURCE, []), 0.90)

    existing_by_url = {
        record.get("url"): record for record in existing if record.get("url")
    }
    combined = []
    seen = set()
    for raw in mof_records + csrc_records + audit_records + ccdi_records:
        key = raw.get("url") or raw.get("sha256")
        if not key or key in seen:
            continue
        seen.add(key)
        raw = dict(raw)
        previous = existing_by_url.get(raw.get("url"))
        if previous:
            raw["case_id"] = previous["case_id"]
            if raw.get("source") == cases.CCDI_SOURCE and (
                not raw.get("body") or raw.get("body") == raw.get("title")
            ):
                raw["body"] = previous.get("body", raw.get("body", ""))
                raw["raw_html"] = previous.get("raw_html", raw.get("raw_html", ""))
        else:
            raw["case_id"] = stable_case_id(raw)
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
