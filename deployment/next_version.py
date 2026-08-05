from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone


def main() -> None:
    parser = argparse.ArgumentParser(description="Choose the next YYYYMMDD.N knowledge release version.")
    parser.add_argument("--repository", default="revercgy-hub/financial-regulations-updates")
    args = parser.parse_args()
    day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{args.repository}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "financial-regulations-updater",
            **(
                {"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
                if os.environ.get("GH_TOKEN")
                else {}
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    sequences = []
    for release in releases:
        match = re.fullmatch(rf"knowledge-{day}\.(\d+)", release.get("tag_name", ""))
        if match:
            sequences.append(int(match.group(1)))
    sequence = max(sequences, default=0) + 1
    if sequence > 99:
        raise SystemExit("Daily release sequence exhausted (maximum 99)")
    print(f"{day}.{sequence}")


if __name__ == "__main__":
    main()
