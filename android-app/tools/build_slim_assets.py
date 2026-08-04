from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT.parent / "金融监管统一知识库_离线包"
DESTINATION = PROJECT / "app" / "build" / "generated" / "slimAssets"
TEMP = DESTINATION.with_name("slimAssets.preparing")

KEEP_FIELDS = (
    "collection",
    "collection_id",
    "category",
    "agency",
    "title",
    "file_no",
    "date",
    "sort_date",
    "status",
    "page_path",
)
SHARD_COUNT = 32
LEGACY_HOME = "../../打开知识库.html"
ANDROID_HOME = "../../index.html"


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def stage_tree(name: str, excluded: set[str] | None = None) -> int:
    source_root = SOURCE / name
    count = 0
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(SOURCE)
        if excluded and relative.as_posix() in excluded:
            continue
        link_or_copy(source, TEMP / relative)
        count += 1
    return count


def stage_documents() -> tuple[int, int]:
    source_root = SOURCE / "docs"
    document_count = 0
    rewritten_links = 0
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(SOURCE)
        destination = TEMP / relative
        if source.suffix.lower() == ".html":
            html = source.read_text(encoding="utf-8")
            occurrences = html.count(LEGACY_HOME)
            if occurrences == 0:
                raise RuntimeError(f"Document has no legacy homepage link: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                html.replace(LEGACY_HOME, ANDROID_HOME),
                encoding="utf-8",
                newline="\n",
            )
            rewritten_links += occurrences
        else:
            link_or_copy(source, destination)
        document_count += 1
    return document_count, rewritten_links


def build_homepage() -> None:
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html, removed = re.subn(
        r"\s*<a href=\"systems/[^\"]+\">.*?</a>",
        "",
        html,
        flags=re.DOTALL,
    )
    if removed != 3:
        raise RuntimeError(f"Expected to remove 3 legacy navigation links, removed {removed}")
    html = re.sub(
        r"<section class=\"usage-note\">.*?</section>",
        """<section class=\"usage-note\">
      <strong>精简完整版</strong>
      <p>保留全部 5,438 篇正文、分类筛选与全文检索；仅移除了与统一正文重复的原系统页面。条文可通过顶部按钮分享、导出 TXT 或保存为 PDF。</p>
      <span>Android 精简版生成于 %s</span>
    </section>""" % datetime.now().astimezone().isoformat(timespec="seconds"),
        html,
        flags=re.DOTALL,
    )
    if "systems/" in html:
        raise RuntimeError("Homepage still contains a removed systems/ link")
    html, replaced = re.subn(
        r'<script src="assets/search-index\.js"></script>',
        '<script src="assets/catalog.js"></script>',
        html,
        count=1,
    )
    if replaced != 1:
        raise RuntimeError("Unable to replace the monolithic search index script")
    (TEMP / "index.html").write_text(html, encoding="utf-8", newline="\n")


def build_app_script() -> None:
    script = (PROJECT / "tools" / "slim-app.js").read_text(encoding="utf-8")
    destination = TEMP / "assets" / "app.js"
    destination.write_text(script, encoding="utf-8", newline="\n")


def build_sharded_index() -> tuple[int, int, int, int]:
    source_path = SOURCE / "assets" / "search-index.js"
    raw = source_path.read_text(encoding="utf-8")
    prefix = "window.KB_DATA="
    if not raw.startswith(prefix):
        raise RuntimeError("Unexpected search-index.js prefix")
    payload = raw[len(prefix):].rstrip()
    if payload.endswith(";"):
        payload = payload[:-1]
    items = json.loads(payload)

    if len(items) != 5438:
        raise RuntimeError(f"Expected 5,438 search records, found {len(items):,}")
    if any(not (SOURCE / item["page_path"]).is_file() for item in items):
        raise RuntimeError("At least one compact-index document path is missing")

    catalog = [{key: item.get(key, "") for key in KEEP_FIELDS} for item in items]
    shard_paths = [f"assets/search-shards/{index:02d}.json" for index in range(SHARD_COUNT)]
    catalog_path = TEMP / "assets" / "catalog.js"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with catalog_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("window.KB_CATALOG=")
        json.dump(catalog, output, ensure_ascii=False, separators=(",", ":"))
        output.write(";window.KB_SEARCH_SHARDS=")
        json.dump(shard_paths, output, ensure_ascii=False, separators=(",", ":"))
        output.write(";")

    shards: list[list[list[object]]] = [[] for _ in range(SHARD_COUNT)]
    shard_weights = [0] * SHARD_COUNT
    search_rows = [(index, item.get("search", "")) for index, item in enumerate(items)]
    # Balance by UTF-8 size rather than document count: several regulations are much
    # longer than average, and an oversized shard would recreate the WebView memory spike.
    for index, search_text in sorted(
        search_rows,
        key=lambda row: len(row[1].encode("utf-8")),
        reverse=True,
    ):
        shard_index = min(range(SHARD_COUNT), key=shard_weights.__getitem__)
        # The original search field is already normalized for lower-case term matching.
        shards[shard_index].append([index, search_text])
        shard_weights[shard_index] += len(search_text.encode("utf-8"))

    shard_root = TEMP / "assets" / "search-shards"
    shard_root.mkdir(parents=True, exist_ok=True)
    shard_bytes = 0
    for index, rows in enumerate(shards):
        path = shard_root / f"{index:02d}.json"
        with path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(rows, output, ensure_ascii=False, separators=(",", ":"))
        shard_bytes += path.stat().st_size
    return len(items), source_path.stat().st_size, catalog_path.stat().st_size, shard_bytes


def main() -> None:
    if not SOURCE.is_dir():
        raise FileNotFoundError(f"Offline knowledge base not found: {SOURCE}")
    shutil.rmtree(TEMP, ignore_errors=True)
    shutil.rmtree(DESTINATION, ignore_errors=True)
    TEMP.mkdir(parents=True)

    build_homepage()
    asset_count = stage_tree(
        "assets",
        excluded={"assets/app.js", "assets/search-index.js"},
    )
    document_count, rewritten_links = stage_documents()
    markdown_count = stage_tree("data/markdown")
    build_app_script()
    record_count, original_index, catalog_size, shard_size = build_sharded_index()

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    TEMP.replace(DESTINATION)
    print(
        "Prepared slim assets: "
        f"{document_count:,} documents, {markdown_count:,} Markdown originals, "
        f"{asset_count:,} supporting assets, "
        f"{rewritten_links:,} Android homepage links rewritten, "
        f"{record_count:,} search records in {SHARD_COUNT} shards; index "
        f"{original_index / 1024 / 1024:.1f} -> "
        f"{catalog_size / 1024 / 1024:.1f} MiB catalog + "
        f"{shard_size / 1024 / 1024:.1f} MiB shards"
    )


if __name__ == "__main__":
    main()
