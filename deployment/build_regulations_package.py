from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DEPLOYMENT = Path(__file__).resolve().parent
BUILD = DEPLOYMENT / "build"
DIST = DEPLOYMENT / "dist"
APP_SCRIPT = PROJECT / "android-app" / "tools" / "slim-app.js"
CASE_KB = PROJECT / "penalty_cases_kb"
CASE_SITE = PROJECT / "penalty_cases_site"
REPOSITORY = "revercgy-hub/financial-regulations-updates"
SCOPE = "regulations"
APP_VERSION = "1.7.4"
MINIMUM_DOCUMENTS = {
    "regulations": 2021,
    "accounting": 1291,
}
EXPECTED_CASE_SOURCES = {"财政部", "证监会", "审计署", "中央纪委国家监委"}
SHARD_COUNT = 16
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


def source_root() -> Path:
    candidates = [
        manifest.parent.parent
        for manifest in PROJECT.glob("*/data/manifest.json")
        if manifest.parent.parent.name != "android-app"
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one unified knowledge base, found {len(candidates)}")
    return candidates[0]


def read_records(source: Path) -> tuple[list[dict[str, object]], dict[str, int]]:
    raw = (source / "assets" / "search-index.js").read_text(encoding="utf-8")
    prefix = "window.KB_DATA="
    if not raw.startswith(prefix):
        raise RuntimeError("Unexpected search-index.js prefix")
    records = json.loads(raw[len(prefix) :].rstrip().rstrip(";"))
    selected = [item for item in records if item.get("collection_id") in MINIMUM_DOCUMENTS]
    counts = {}
    for collection, minimum in MINIMUM_DOCUMENTS.items():
        actual = sum(item.get("collection_id") == collection for item in selected)
        counts[collection] = actual
        if actual < minimum:
            raise RuntimeError(
                f"Expected at least {minimum:,} {collection} records, found {actual:,}"
            )
    return selected, counts


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def build_homepage(
    source: Path,
    destination: Path,
    generated_at: str,
    version: str,
    collection_counts: dict[str, int],
    case_documents: int,
) -> None:
    html = (source / "index.html").read_text(encoding="utf-8")
    case_card = f"""<article class="collection-card red case-library-card" aria-pressed="false">
          <div class="collection-card-main">
            <span class="card-label">案例库</span>
            <strong>{case_documents:,}</strong>
            <small>财政部、证监会、审计署、中央纪委国家监委公开案例</small>
          </div>
          <div class="collection-guide">按来源、年份、主体、案由和处理结果查询</div>
          <div class="collection-actions" style="grid-template-columns:1fr">
            <a href="cases/index.html">进入案例库 <span>→</span></a>
          </div>
        </article>"""
    html, case_card_count = re.subn(
        r'\s*<article class="collection-card red".*?</article>',
        "\n" + case_card,
        html,
        flags=re.DOTALL,
    )
    if case_card_count != 1:
        raise RuntimeError(f"Expected one case card placeholder, found {case_card_count}")
    html = re.sub(
        r'\s*<a href="systems/(?:regulations|accounting)/index\.html">.*?</a>',
        "",
        html,
    )
    replacements = {
        "FINANCIAL REGULATION · OFFLINE LIBRARY": "FINANCIAL, ACCOUNTING & CASES · ONLINE SYNC",
        "金融监管统一知识库": "金融、会计与案例知识库",
        "金融监管制度、财政部和证监会处罚案例、会计制度，一处检索，完全离线。":
            "金融监管制度、会计制度与财政、证监、审计、纪检监察案例，联网同步更新。",
        "在统一库中筛选": "在制度库中筛选",
        "统一全文检索": "金融与会计制度检索",
        "支持标题、正文、文号、机构、当事人、案由和处罚类型":
            "支持标题、正文、文号、机构、分类、状态和年份",
        "例如：内幕交易、资本管理、会计准则第14号、银监发":
            "例如：资本管理、会计准则第14号、内部控制、银监发",
        "正在载入离线索引…": "正在载入金融与会计制度索引…",
        "<div class=\"offline-badge\"><span></span>离线可用</div>":
            "<div class=\"offline-badge\"><span></span>已联网同步</div>",
        '<script src="assets/search-index.js"></script>':
            f'<script src="assets/catalog.js?v={version}"></script>',
        '<script src="assets/app.js"></script>':
            f'<script src="assets/app.js?v={version}"></script>',
    }
    for old, new in replacements.items():
        if old not in html:
            raise RuntimeError(f"Homepage marker not found: {old}")
        html = html.replace(old, new)
    statistic_replacements = (
        (
            r"<strong>[\d,]+</strong><span>篇文档</span>",
            f"<strong>{sum(collection_counts.values()) + case_documents:,}</strong><span>篇资料</span>",
        ),
        (
            r"<strong>\d+</strong><span>个分类</span>",
            "<strong>4</strong><span>个案例来源</span>",
        ),
        (
            r"<strong>3</strong><span>套来源库</span>",
            "<strong>3</strong><span>套在线知识库</span>",
        ),
    )
    for pattern, replacement in statistic_replacements:
        html, count = re.subn(pattern, replacement, html, count=1)
        if count != 1:
            raise RuntimeError(f"Homepage statistic marker not found: {pattern}")
    html = re.sub(
        r'<section class="usage-note">.*?</section>',
        """<section class="usage-note">
      <strong>联网同步版</strong>
      <p>本地内容由 APP 从 GitHub 安全下载并校验；金融监管制度、会计制度和案例库随同一版本自动更新。</p>
      <p><a href="cases/index.html">进入财政、证监会、审计、纪检监察案例库</a></p>
      <span>知识库生成于 %s</span>
    </section>""" % generated_at,
        html,
        flags=re.DOTALL,
    )
    destination.write_text(html, encoding="utf-8", newline="\n")


def stage_documents(source: Path, package: Path, records: list[dict[str, object]]) -> None:
    for item in records:
        relative = Path(str(item["page_path"]))
        source_html = source / relative
        if not source_html.is_file():
            raise FileNotFoundError(source_html)
        html = source_html.read_text(encoding="utf-8")
        html = html.replace("../../打开知识库.html", "../../index.html")
        destination_html = package / relative
        destination_html.parent.mkdir(parents=True, exist_ok=True)
        destination_html.write_text(html, encoding="utf-8", newline="\n")

        markdown_relative = Path("data/markdown") / relative.relative_to("docs")
        markdown_relative = markdown_relative.with_suffix(".md")
        source_markdown = source / markdown_relative
        if not source_markdown.is_file():
            raise FileNotFoundError(source_markdown)
        copy_file(source_markdown, package / markdown_relative)


def build_index(package: Path, records: list[dict[str, object]]) -> None:
    catalog = [{key: item.get(key, "") for key in KEEP_FIELDS} for item in records]
    shard_paths = [f"assets/search-shards/{index:02d}.json" for index in range(SHARD_COUNT)]
    catalog_path = package / "assets" / "catalog.js"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with catalog_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("window.KB_CATALOG=")
        json.dump(catalog, output, ensure_ascii=False, separators=(",", ":"))
        output.write(";window.KB_SEARCH_SHARDS=")
        json.dump(shard_paths, output, ensure_ascii=False, separators=(",", ":"))
        output.write(";")

    shards: list[list[list[object]]] = [[] for _ in range(SHARD_COUNT)]
    weights = [0] * SHARD_COUNT
    rows = [(index, str(item.get("search", ""))) for index, item in enumerate(records)]
    for index, search_text in sorted(
        rows, key=lambda row: len(row[1].encode("utf-8")), reverse=True
    ):
        shard_index = min(range(SHARD_COUNT), key=weights.__getitem__)
        shards[shard_index].append([index, search_text])
        weights[shard_index] += len(search_text.encode("utf-8"))

    shard_root = package / "assets" / "search-shards"
    shard_root.mkdir(parents=True, exist_ok=True)
    for index, shard in enumerate(shards):
        with (shard_root / f"{index:02d}.json").open(
            "w", encoding="utf-8", newline="\n"
        ) as output:
            json.dump(shard, output, ensure_ascii=False, separators=(",", ":"))


def stage_cases(package: Path) -> tuple[int, dict[str, int]]:
    manifest_path = CASE_KB / "manifest.json"
    site_manifest_path = CASE_SITE / "site_manifest.json"
    if not manifest_path.is_file() or not site_manifest_path.is_file():
        raise RuntimeError("Case library has not been built")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    site_manifest = json.loads(site_manifest_path.read_text(encoding="utf-8"))
    documents = int(manifest.get("documents") or 0)
    by_source = {str(key): int(value) for key, value in manifest.get("by_source", {}).items()}
    if documents <= 0 or documents != int(site_manifest.get("documents") or 0):
        raise RuntimeError("Case knowledge base and static site counts do not match")
    if set(by_source) != EXPECTED_CASE_SOURCES or sum(by_source.values()) != documents:
        raise RuntimeError(f"Unexpected case source counts: {by_source}")

    for source in CASE_SITE.rglob("*"):
        if source.is_file():
            copy_file(source, package / "cases" / source.relative_to(CASE_SITE))
    case_index = (package / "cases" / "index.html").read_text(encoding="utf-8")
    for marker in (
        "const initialParams = new URLSearchParams(location.search)",
        "sessionStorage.setItem('caseFilters'",
        "history.replaceState",
    ):
        if marker not in case_index:
            raise RuntimeError(f"Case site is missing navigation-state support: {marker}")
    markdown_root = CASE_KB / "markdown"
    for source in markdown_root.rglob("*.md"):
        copy_file(source, package / "case-data" / "markdown" / source.relative_to(markdown_root))
    return documents, by_source


def zip_tree(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())


def validate_android_paths(source: Path) -> None:
    violations = []
    for path in source.rglob("*"):
        for component in path.relative_to(source).parts:
            size = len(component.encode("utf-8"))
            if size > 255:
                violations.append(f"{size} bytes: {path.relative_to(source)}")
                break
    if violations:
        preview = "\n".join(violations[:10])
        raise RuntimeError(f"Package contains Android-incompatible filenames:\n{preview}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Data version, for example 20260805.1")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}\.[0-9]+", args.version):
        raise SystemExit("--version must use YYYYMMDD.N format")

    day, sequence = args.version.split(".", 1)
    if int(sequence) > 99:
        raise SystemExit("At most 99 releases are supported per day")
    version_code = int(day) * 100 + int(sequence)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    source = source_root()
    records, collection_counts = read_records(source)
    package = BUILD / f"regulations-{args.version}"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    DIST.mkdir(parents=True, exist_ok=True)

    case_documents, case_by_source = stage_cases(package)
    build_homepage(
        source,
        package / "index.html",
        generated_at,
        args.version,
        collection_counts,
        case_documents,
    )
    copy_file(source / "assets" / "site.css", package / "assets" / "site.css")
    copy_file(APP_SCRIPT, package / "assets" / "app.js")
    stage_documents(source, package, records)
    build_index(package, records)
    regulation_documents = collection_counts["regulations"]
    accounting_documents = collection_counts["accounting"]
    package_manifest = {
        "schema": 1,
        "scope": SCOPE,
        "version": args.version,
        "version_code": version_code,
        "generated_at": generated_at,
        "documents": len(records),
        "regulation_documents": regulation_documents,
        "accounting_documents": accounting_documents,
        "case_documents": case_documents,
        "case_by_source": case_by_source,
    }
    (package / "package.json").write_text(
        json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    validate_android_paths(package)

    asset_name = f"knowledge-package-{args.version}.zip"
    archive = DIST / asset_name
    zip_tree(package, archive)
    digest = sha256(archive)
    tag = f"knowledge-{args.version}"
    latest = {
        **package_manifest,
        "min_app_version_code": 8,
        "package_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}",
        "package_size": archive.stat().st_size,
        "sha256": digest,
        "app_version": APP_VERSION,
        "app_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/FinReg-KnowledgeBase-Online-v{APP_VERSION}.apk",
    }
    latest_path = DIST / "latest.json"
    latest_path.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Built {archive.name}: {archive.stat().st_size / 1024 / 1024:.1f} MiB, "
        f"{regulation_documents:,} regulations + {accounting_documents:,} accounting + "
        f"{case_documents:,} cases, sha256={digest}"
    )
    print(f"Manifest: {latest_path}")


if __name__ == "__main__":
    main()
