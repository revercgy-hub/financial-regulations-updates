import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sqlite3
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

import markdown
import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "金融监管统一知识库_离线包"
DEFAULT_ZIP = ROOT / "金融监管统一知识库_离线包.zip"

COLLECTIONS = [
    {
        "id": "regulations",
        "name": "金融监管制度",
        "root": ROOT / "iweicha_ffs_kb",
        "site_root": ROOT / "iweicha_ffs_site",
        "description": "人民银行、国家金融监督管理总局、国家外汇管理局监管制度",
        "navigation": "按监管机构、文件状态、年份和文号浏览",
        "color": "teal",
    },
    {
        "id": "penalties",
        "name": "财政部和证监会处罚库",
        "root": ROOT / "penalty_cases_kb",
        "site_root": ROOT / "penalty_cases_site",
        "description": "财政部与中国证监会行政处罚决定及结构化案情信息",
        "navigation": "按来源、主体、处罚类型和案由分类浏览",
        "color": "red",
    },
    {
        "id": "accounting",
        "name": "会计制度",
        "root": ROOT / "maodocs_kb",
        "site_root": ROOT / "maodocs_site",
        "description": "会计、审计、证券、内控、评估相关制度与执业资料",
        "navigation": "按会计、审计、证券、内控、评估目录浏览",
        "color": "gold",
    },
]

MAODOCS_SECTIONS = {
    "accounting": "会计",
    "auditing": "审计",
    "securities": "证券",
    "control": "内控",
    "appraisal": "评估",
}


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def strip_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, text[end + 5 :]


def plain_text(markdown_text: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown_text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#>*_`|~-]+", " ", text)
    return clean_text(text)


def markdown_body(markdown_text: str) -> str:
    _, body = strip_frontmatter(markdown_text)
    marker = "\n## 正文\n"
    if marker in body:
        return body.split(marker, 1)[1].strip()
    return body.strip()


def source_url(record: dict, meta: dict) -> str:
    value = clean_text(
        record.get("source_link")
        or record.get("url")
        or record.get("source_url")
        or meta.get("source_link")
        or meta.get("source_url")
        or ""
    )
    return value if value.startswith(("http://", "https://")) else ""


def record_category(collection_id: str, record: dict) -> str:
    if collection_id == "regulations":
        return clean_text(record.get("agency") or "其他")
    if collection_id == "penalties":
        return clean_text(record.get("source") or record.get("agency") or "其他")
    rel = PurePosixPath(str(record.get("markdown_path", "")).replace("\\", "/"))
    parts = rel.parts
    section = parts[1] if len(parts) > 1 and parts[0] == "markdown" else (parts[0] if parts else "")
    return MAODOCS_SECTIONS.get(section, "综合")


def record_date(collection_id: str, record: dict) -> str:
    if collection_id == "regulations":
        return clean_text(str(record.get("year") or ""))
    if collection_id == "penalties":
        return clean_text(str(record.get("publish_date") or ""))
    return clean_text(str(record.get("lastmod") or ""))[:10]


def record_agency(collection_id: str, record: dict, category: str) -> str:
    if collection_id == "regulations":
        return clean_text(record.get("agency") or category)
    if collection_id == "penalties":
        return clean_text(record.get("agency") or record.get("source") or category)
    return category


def record_tags(collection_id: str, record: dict) -> list[str]:
    values = []
    if collection_id == "regulations":
        values.extend([record.get("file_class", ""), record.get("level", ""), record.get("state", "")])
    elif collection_id == "penalties":
        values.extend(
            [
                record.get("party_type", ""),
                record.get("party_role", ""),
                record.get("violation_type", ""),
                record.get("penalty_types", ""),
            ]
        )
    else:
        values.append(record.get("description", ""))
    return [clean_text(str(value)) for value in values if clean_text(str(value or ""))]


def load_documents() -> list[dict]:
    documents = []
    for collection in COLLECTIONS:
        index_path = collection["root"] / "index.jsonl"
        if not index_path.is_file():
            raise FileNotFoundError(f"缺少索引文件：{index_path}")
        rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for sequence, record in enumerate(rows, start=1):
            source_markdown = collection["root"] / record["markdown_path"]
            if not source_markdown.is_file():
                raise FileNotFoundError(f"缺少正文：{source_markdown}")
            raw = source_markdown.read_text(encoding="utf-8", errors="replace")
            meta, content = strip_frontmatter(raw)
            body = markdown_body(raw)
            title = clean_text(str(record.get("title") or meta.get("title") or source_markdown.stem))
            category = record_category(collection["id"], record)
            agency = record_agency(collection["id"], record, category)
            file_no = clean_text(str(record.get("file_no") or ""))
            date = record_date(collection["id"], record)
            status = clean_text(str(record.get("state") or ""))
            tags = record_tags(collection["id"], record)
            summary = clean_text(str(record.get("summary") or "")) or plain_text(body)[:520]
            doc_key = f"{collection['id']}-{sequence:05d}"
            page_path = f"docs/{collection['id']}/{sequence:05d}.html"
            packaged_markdown = f"data/markdown/{collection['id']}/{sequence:05d}.md"
            documents.append(
                {
                    "id": doc_key,
                    "sequence": sequence,
                    "collection_id": collection["id"],
                    "collection": collection["name"],
                    "category": category,
                    "agency": agency,
                    "title": title,
                    "file_no": file_no,
                    "date": date,
                    "status": status,
                    "tags": tags,
                    "source_url": source_url(record, meta),
                    "source_root": collection["root"],
                    "source_markdown": source_markdown,
                    "source_markdown_rel": str(record["markdown_path"]).replace("\\", "/"),
                    "packaged_markdown": packaged_markdown,
                    "page_path": page_path,
                    "raw_markdown": raw,
                    "markdown_content": content,
                    "body_text": plain_text(body),
                    "summary": summary,
                    "content_sha256": hashlib.sha256(clean_text(body).encode("utf-8")).hexdigest(),
                    "record": record,
                }
            )
    return documents


def normalize_url(value: str, base: str = "") -> str:
    value = value.strip().strip("<>")
    if value.startswith("//"):
        value = "https:" + value
    elif base:
        value = urljoin(base, value)
    parsed = urlparse(value)
    if not parsed.scheme:
        return value
    normalized = parsed._replace(fragment="").geturl()
    return normalized.rstrip("/")


IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")


def external_image_urls(documents: list[dict]) -> set[str]:
    urls = set()
    for document in documents:
        base = document.get("source_url") or ""
        for match in IMAGE_PATTERN.finditer(document["markdown_content"]):
            target = match.group(2).strip("<>")
            if target.startswith("data:"):
                continue
            normalized = normalize_url(target, base)
            if normalized.startswith(("http://", "https://")):
                urls.add(normalized)
    return urls


def media_filename(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(unquote(parsed.path)).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        suffix = ".bin"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(unquote(parsed.path)).stem)[:70].strip("._") or "image"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"{digest}_{stem}{suffix}"


def download_media(urls: set[str], output: Path, workers: int = 10) -> tuple[dict[str, str], list[dict]]:
    media_dir = output / "assets" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    mappings = {}
    failures = []

    def fetch(url: str) -> tuple[str, str, str]:
        filename = media_filename(url)
        destination = media_dir / filename
        session = requests.Session()
        session.headers.update({"User-Agent": "UnifiedOfflineKnowledgeBase/1.0"})
        response = session.get(url, timeout=35)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return url, filename, response.headers.get("content-type", "")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, url): url for url in sorted(urls)}
        for future in as_completed(futures):
            url = futures[future]
            try:
                source, filename, _ = future.result()
                mappings[source] = f"../../assets/media/{filename}"
            except Exception as exc:
                failures.append({"url": url, "error": str(exc)})
    return mappings, failures


def build_link_maps(documents: list[dict]) -> tuple[dict, dict]:
    url_map = {}
    path_map = {}
    for document in documents:
        page = "../../" + document["page_path"]
        rel = document["source_markdown_rel"].replace("\\", "/")
        path_map[(document["collection_id"], rel)] = page
        if rel.startswith("markdown/"):
            path_map[(document["collection_id"], rel[len("markdown/") :])] = page
        if document["source_url"]:
            normalized = normalize_url(document["source_url"])
            url_map[normalized] = page
            if normalized.endswith(".html"):
                url_map[normalized[:-5] + ".md"] = page
    return url_map, path_map


def resolve_internal_link(target: str, document: dict, url_map: dict, path_map: dict) -> str | None:
    target = target.strip().strip("<>")
    if target.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    fragment = ""
    if "#" in target:
        target, fragment = target.split("#", 1)
        fragment = "#" + fragment
    normalized_url = normalize_url(target, document.get("source_url") or "")
    if normalized_url in url_map:
        return url_map[normalized_url] + fragment
    if document["collection_id"] == "accounting":
        parsed = urlparse(target)
        target_path = unquote(parsed.path).replace("\\", "/")
        candidates = []
        if parsed.netloc == "docs.maoyanqing.com" or target_path.startswith("/"):
            candidates.append("markdown/" + target_path.lstrip("/"))
            candidates.append(target_path.lstrip("/"))
        elif not parsed.scheme:
            current = PurePosixPath(document["source_markdown_rel"])
            candidates.append(posixpath.normpath(posixpath.join(str(current.parent), target_path)))
            candidates.append(posixpath.normpath(target_path))
        for candidate in candidates:
            candidate = candidate.rstrip("/")
            if candidate.endswith(".html"):
                candidate = candidate[:-5] + ".md"
            elif not candidate.endswith(".md"):
                candidate += "/index.md"
            for variant in (candidate, candidate.removeprefix("markdown/"), "markdown/" + candidate.removeprefix("markdown/")):
                key = (document["collection_id"], variant)
                if key in path_map:
                    return path_map[key] + fragment
    return None


def rewrite_markdown(markdown_text: str, document: dict, url_map: dict, path_map: dict, media_map: dict) -> str:
    def replace_image(match: re.Match) -> str:
        label = match.group(1)
        original = match.group(2).strip("<>")
        normalized = normalize_url(original, document.get("source_url") or "")
        target = media_map.get(normalized, original)
        return f"![{label}]({target})"

    text = IMAGE_PATTERN.sub(replace_image, markdown_text)

    def replace_link(match: re.Match) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        if target.strip("<>") in {"空缺", "无", "暂无", "不详"}:
            return label
        internal = resolve_internal_link(target, document, url_map, path_map)
        if internal:
            return f"[{label}]({internal})"
        return match.group(0)

    return re.sub(r"(?<!!)\[([^\]]+)\]\((<[^>]+>|[^)]+)\)", replace_link, text)


def chips(values: list[str]) -> str:
    return "".join(f"<span>{html.escape(value)}</span>" for value in values if value)


def document_page(document: dict, rendered: str) -> str:
    metadata = [document["collection"], document["category"], document["date"], document["status"], document["file_no"]]
    source_link = ""
    if document["source_url"]:
        source_link = (
            f'<a class="side-link external" href="{html.escape(document["source_url"])}" '
            'target="_blank" rel="noreferrer">查看在线来源</a>'
        )
    tag_text = "、".join(document["tags"]) or "未标注"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{html.escape(document['title'])}｜金融监管统一知识库</title>
  <link rel="stylesheet" href="../../assets/site.css">
</head>
<body class="document-body">
  <header class="topbar">
    <a class="brand" href="../../打开知识库.html"><span class="brand-mark">规</span>金融监管统一知识库</a>
    <form class="top-search" action="../../打开知识库.html">
      <label class="sr-only" for="docSearch">搜索知识库</label>
      <input id="docSearch" name="q" placeholder="搜索制度、文号、当事人、条款" autocomplete="off">
      <button type="submit">搜索</button>
    </form>
  </header>
  <main class="doc-layout">
    <article class="doc-card">
      <nav class="breadcrumb"><a href="../../打开知识库.html">统一知识库</a><span>›</span><a href="../../打开知识库.html?collection={document['collection_id']}">{html.escape(document['collection'])}</a><span>›</span>{html.escape(document['category'])}</nav>
      <h1>{html.escape(document['title'])}</h1>
      <div class="meta-row">{chips(metadata)}</div>
      <div class="prose">{rendered}</div>
    </article>
    <aside class="side-card">
      <a class="side-link primary" href="../../打开知识库.html">返回统一检索</a>
      <a class="side-link" href="../../{document['packaged_markdown']}">打开 Markdown 原文</a>
      {source_link}
      <dl>
        <dt>资料库</dt><dd>{html.escape(document['collection'])}</dd>
        <dt>分类</dt><dd>{html.escape(document['category'])}</dd>
        <dt>机构</dt><dd>{html.escape(document['agency'] or '未标注')}</dd>
        <dt>日期</dt><dd>{html.escape(document['date'] or '未标注')}</dd>
        <dt>标签</dt><dd>{html.escape(tag_text)}</dd>
      </dl>
    </aside>
  </main>
</body>
</html>
"""


def index_page(manifest: dict) -> str:
    collection_cards = "\n".join(
        f"""<article class="collection-card {collection['color']}" data-collection="{collection['id']}" aria-pressed="false">
          <div class="collection-card-main">
            <span class="card-label">{html.escape(collection['name'])}</span>
            <strong>{manifest['by_collection'].get(collection['name'], 0):,}</strong>
            <small>{html.escape(collection['description'])}</small>
          </div>
          <div class="collection-guide">{html.escape(collection['navigation'])}</div>
          <div class="collection-actions">
            <button class="collection-filter-action" type="button">在统一库中筛选</button>
            <a href="systems/{collection['id']}/index.html">进入原系统导览 <span>→</span></a>
          </div>
        </article>"""
        for collection in COLLECTIONS
    )
    generated = html.escape(manifest["generated_at"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>金融监管统一知识库</title>
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div class="eyebrow">FINANCIAL REGULATION · OFFLINE LIBRARY</div>
      <h1>金融监管统一知识库</h1>
      <p>金融监管制度、财政部和证监会处罚案例、会计制度，一处检索，完全离线。</p>
      <div class="hero-stats">
        <div><strong>{manifest['documents']:,}</strong><span>篇文档</span></div>
        <div><strong>{len(manifest['categories'])}</strong><span>个分类</span></div>
        <div><strong>3</strong><span>套来源库</span></div>
      </div>
    </div>
  </header>
  <main class="home-shell">
    <section class="collection-grid" aria-label="资料库筛选">
      {collection_cards}
    </section>
    <section class="search-panel">
      <div class="search-heading">
        <div><h2>统一全文检索</h2><p>支持标题、正文、文号、机构、当事人、案由和处罚类型</p></div>
        <button id="resetAll" class="text-button" type="button">清除筛选</button>
      </div>
      <div class="search-box">
        <label class="sr-only" for="searchInput">输入关键词</label>
        <span class="search-icon" aria-hidden="true">⌕</span>
        <input id="searchInput" placeholder="例如：内幕交易、资本管理、会计准则第14号、银监发" autocomplete="off" autofocus>
        <kbd>Enter</kbd>
      </div>
      <div class="filters">
        <label>资料库<select id="collectionFilter"><option value="">全部资料库</option></select></label>
        <label>分类<select id="categoryFilter"><option value="">全部分类</option></select></label>
        <label>年份<select id="yearFilter"><option value="">全部年份</option></select></label>
      </div>
      <div class="result-toolbar">
        <div id="resultMeta">正在载入离线索引…</div>
        <div class="offline-badge"><span></span>离线可用</div>
      </div>
      <div id="results" class="results" aria-live="polite"></div>
      <button id="loadMore" class="load-more" type="button" hidden>显示更多</button>
    </section>
    <section class="usage-note">
      <strong>使用提示</strong>
      <p>双击本文件即可检索，无需联网。需要更精确、不限结果数的检索时，可使用包内 SQLite 全文库和 <code>search_kb.py</code>。</p>
      <span>生成于 {generated}</span>
    </section>
  </main>
  <script src="assets/search-index.js"></script>
  <script src="assets/app.js"></script>
</body>
</html>
"""


def site_css() -> str:
    return r"""
:root {
  --ink: #13242a;
  --muted: #617078;
  --paper: #ffffff;
  --canvas: #f3f5f2;
  --line: #dce2dd;
  --teal: #0e625c;
  --teal-dark: #094a46;
  --gold: #a87322;
  --red: #93443b;
  --shadow: 0 14px 40px rgba(24, 47, 48, .08);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--canvas);
  color: var(--ink);
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif;
  text-rendering: optimizeLegibility;
}
a { color: var(--teal); text-decoration: none; }
button, input, select { font: inherit; }
.sr-only { clip: rect(0 0 0 0); clip-path: inset(50%); height: 1px; overflow: hidden; position: absolute; white-space: nowrap; width: 1px; }
.hero {
  background:
    radial-gradient(circle at 82% 12%, rgba(186, 143, 68, .22), transparent 26%),
    linear-gradient(128deg, #0a4844 0%, #0d5d57 56%, #154d4d 100%);
  color: #fff;
  overflow: hidden;
  position: relative;
}
.hero::after { background: repeating-linear-gradient(90deg, transparent 0 84px, rgba(255,255,255,.035) 84px 85px); content: ""; inset: 0; position: absolute; }
.hero-inner { margin: auto; max-width: 1180px; padding: 54px 28px 48px; position: relative; z-index: 1; }
.eyebrow { color: #c8dcd7; font-family: Consolas, monospace; font-size: 12px; letter-spacing: .18em; margin-bottom: 14px; }
.hero h1 { font-family: "STSong", "SimSun", serif; font-size: clamp(36px, 6vw, 62px); font-weight: 700; letter-spacing: .06em; margin: 0; }
.hero p { color: #dce9e6; font-size: 17px; margin: 14px 0 28px; }
.hero-stats { display: flex; gap: 30px; }
.hero-stats div { align-items: baseline; display: flex; gap: 8px; }
.hero-stats strong { font-family: Georgia, serif; font-size: 25px; }
.hero-stats span { color: #c4d8d4; font-size: 13px; }
.home-shell { margin: -18px auto 60px; max-width: 1180px; padding: 0 24px; position: relative; z-index: 2; }
.collection-grid { display: grid; gap: 14px; grid-template-columns: repeat(3, 1fr); }
.collection-card {
  background: var(--paper); border: 1px solid rgba(255,255,255,.7); border-radius: 12px; box-shadow: var(--shadow); color: var(--ink);
  display: flex; flex-direction: column; min-height: 224px; padding: 20px 22px; position: relative; text-align: left; transition: transform .18s ease, border-color .18s ease;
}
.collection-card::before { background: var(--teal); border-radius: 99px; content: ""; height: 4px; left: 22px; position: absolute; top: 0; width: 54px; }
.collection-card.red::before { background: var(--red); }
.collection-card.gold::before { background: var(--gold); }
.collection-card:hover { transform: translateY(-2px); }
.collection-card[aria-pressed="true"] { border-color: var(--teal); box-shadow: 0 12px 34px rgba(14,98,92,.16); }
.card-label { display: block; font-size: 16px; font-weight: 700; margin-bottom: 10px; }
.collection-card strong { display: block; font-family: Georgia, serif; font-size: 31px; line-height: 1; }
.collection-card small { color: var(--muted); display: block; line-height: 1.45; margin-top: 10px; }
.collection-card-main { flex: 1; }
.collection-guide { border-top: 1px solid #e8ece8; color: #66736f; font-size: 12px; line-height: 1.5; margin-top: 14px; padding-top: 12px; }
.collection-actions { display: grid; gap: 8px; grid-template-columns: 1fr 1.18fr; margin-top: 13px; }
.collection-actions button, .collection-actions a { align-items: center; border-radius: 6px; cursor: pointer; display: flex; font-size: 12px; justify-content: center; min-height: 36px; padding: 7px 8px; text-align: center; }
.collection-actions button { background: #f1f5f2; border: 1px solid #d8e1dc; color: #52615d; }
.collection-actions a { background: var(--teal); border: 1px solid var(--teal); color: white; font-weight: 700; gap: 5px; }
.collection-card.red .collection-actions a { background: var(--red); border-color: var(--red); }
.collection-card.gold .collection-actions a { background: var(--gold); border-color: var(--gold); }
.search-panel { background: var(--paper); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); margin-top: 18px; padding: 28px; }
.search-heading { align-items: end; display: flex; justify-content: space-between; }
.search-heading h2 { font-family: "STSong", "SimSun", serif; font-size: 25px; margin: 0 0 4px; }
.search-heading p { color: var(--muted); font-size: 13px; margin: 0; }
.text-button { background: none; border: 0; color: var(--teal); cursor: pointer; padding: 6px; }
.search-box { align-items: center; background: #f8faf8; border: 1px solid #bac9c3; border-radius: 10px; display: flex; margin-top: 20px; padding: 4px 8px 4px 15px; }
.search-box:focus-within { border-color: var(--teal); box-shadow: 0 0 0 3px rgba(14,98,92,.11); }
.search-icon { color: var(--teal); font-family: Georgia, serif; font-size: 27px; margin-right: 10px; transform: rotate(-18deg); }
.search-box input { background: transparent; border: 0; color: var(--ink); flex: 1; font-size: 17px; min-width: 0; outline: none; padding: 13px 0; }
.search-box kbd { background: #fff; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px; color: #84918d; font-size: 11px; padding: 4px 7px; }
.filters { display: grid; gap: 12px; grid-template-columns: repeat(3, 1fr); margin-top: 14px; }
.filters label { color: var(--muted); font-size: 12px; }
.filters select { background: #fff; border: 1px solid var(--line); border-radius: 7px; color: var(--ink); display: block; margin-top: 5px; padding: 9px 10px; width: 100%; }
.result-toolbar { align-items: center; border-bottom: 1px solid var(--line); color: var(--muted); display: flex; font-size: 13px; justify-content: space-between; margin-top: 22px; padding-bottom: 11px; }
.offline-badge { align-items: center; display: flex; gap: 7px; }
.offline-badge span { background: #3c9671; border-radius: 50%; box-shadow: 0 0 0 3px rgba(60,150,113,.13); height: 7px; width: 7px; }
.results { display: grid; gap: 0; }
.result { border-bottom: 1px solid #e7ebe7; color: var(--ink); display: block; padding: 19px 4px; }
.result:hover .result-title { color: var(--teal); }
.result-kicker { align-items: center; color: var(--muted); display: flex; font-size: 12px; gap: 8px; margin-bottom: 6px; }
.result-kicker b { border: 1px solid #cbd8d3; border-radius: 99px; color: var(--teal); font-size: 11px; padding: 3px 7px; }
.result-title { font-family: "STSong", "SimSun", serif; font-size: 19px; font-weight: 700; line-height: 1.42; }
.result-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.result-meta span { background: #f1f4f1; border-radius: 4px; color: #5f6b6d; font-size: 11px; padding: 4px 7px; }
.result p { color: #4c595d; display: -webkit-box; font-size: 13px; line-height: 1.65; margin: 9px 0 0; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
mark { background: #f5e8bd; color: inherit; padding: 0 1px; }
.empty { color: var(--muted); padding: 48px 8px; text-align: center; }
.empty strong { color: var(--ink); display: block; font-family: "STSong", "SimSun", serif; font-size: 20px; margin-bottom: 8px; }
.load-more { background: #fff; border: 1px solid #b8c8c2; border-radius: 8px; color: var(--teal); cursor: pointer; display: block; margin: 18px auto 0; padding: 10px 26px; }
.usage-note { align-items: center; color: var(--muted); display: grid; font-size: 12px; gap: 12px; grid-template-columns: auto 1fr auto; margin: 18px 8px 0; }
.usage-note strong { color: var(--ink); }
.usage-note p { margin: 0; }
.usage-note code { background: #e7ece8; border-radius: 3px; padding: 2px 4px; }
.topbar { align-items: center; backdrop-filter: blur(10px); background: rgba(250,251,248,.95); border-bottom: 1px solid var(--line); display: flex; gap: 24px; justify-content: space-between; padding: 10px max(22px, calc((100vw - 1180px) / 2)); position: sticky; top: 0; z-index: 20; }
.brand { align-items: center; color: var(--ink); display: flex; font-family: "STSong", "SimSun", serif; font-size: 17px; font-weight: 700; gap: 9px; white-space: nowrap; }
.brand-mark { align-items: center; background: var(--teal); border-radius: 5px; color: white; display: inline-flex; font-family: "STSong", "SimSun", serif; height: 29px; justify-content: center; width: 29px; }
.top-search { display: flex; max-width: 520px; width: 48%; }
.top-search input { border: 1px solid var(--line); border-radius: 7px 0 0 7px; min-width: 0; padding: 9px 11px; width: 100%; }
.top-search button { background: var(--teal); border: 0; border-radius: 0 7px 7px 0; color: white; padding: 0 16px; }
.doc-layout { display: grid; gap: 20px; grid-template-columns: minmax(0, 1fr) 226px; margin: 24px auto 64px; max-width: 1180px; padding: 0 24px; }
.doc-card, .side-card { background: var(--paper); border: 1px solid var(--line); border-radius: 11px; box-shadow: 0 8px 28px rgba(24,47,48,.05); }
.doc-card { min-width: 0; padding: 30px 38px 50px; }
.breadcrumb { color: var(--muted); display: flex; flex-wrap: wrap; font-size: 12px; gap: 7px; margin-bottom: 19px; }
.doc-card > h1 { font-family: "STSong", "SimSun", serif; font-size: clamp(27px, 4vw, 38px); line-height: 1.38; margin: 0; }
.meta-row { display: flex; flex-wrap: wrap; gap: 7px; margin: 16px 0 28px; }
.meta-row span { background: #edf2ef; border-radius: 99px; color: #556361; font-size: 12px; padding: 5px 9px; }
.prose { border-top: 1px solid var(--line); font-family: "STSong", "SimSun", serif; font-size: 16px; line-height: 1.85; padding-top: 21px; }
.prose h1 { font-size: 28px; }
.prose h2 { border-left: 3px solid var(--teal); font-size: 22px; margin-top: 34px; padding-left: 11px; }
.prose h3 { font-size: 18px; margin-top: 28px; }
.prose img { display: block; height: auto; margin: 18px auto; max-width: 100%; }
.prose table { border-collapse: collapse; display: block; font-family: "Microsoft YaHei", sans-serif; font-size: 13px; overflow-x: auto; width: 100%; }
.prose th, .prose td { border: 1px solid var(--line); min-width: 90px; padding: 7px 9px; text-align: left; vertical-align: top; }
.prose th { background: #eef2ef; }
.prose blockquote { border-left: 3px solid #bd9a59; color: #596465; margin-left: 0; padding-left: 16px; }
.prose pre { background: #172527; border-radius: 7px; color: #e7efeb; overflow-x: auto; padding: 14px; }
.side-card { align-self: start; padding: 14px; position: sticky; top: 74px; }
.side-link { border-bottom: 1px solid #edf0ed; display: block; font-size: 13px; padding: 10px 9px; }
.side-link.primary { background: var(--teal); border-radius: 6px; color: white; margin-bottom: 6px; text-align: center; }
.side-link.external::after { content: " ↗"; }
.side-card dl { font-size: 12px; margin: 12px 9px 4px; }
.side-card dt { color: #87918f; margin-top: 13px; }
.side-card dd { line-height: 1.55; margin: 3px 0 0; overflow-wrap: anywhere; }
@media (max-width: 820px) {
  .collection-grid, .filters { grid-template-columns: 1fr; }
  .hero-inner { padding-top: 38px; }
  .hero-stats { flex-wrap: wrap; gap: 12px 24px; }
  .home-shell { padding: 0 14px; }
  .search-panel { padding: 21px 17px; }
  .search-heading { align-items: start; gap: 10px; }
  .usage-note { align-items: start; grid-template-columns: 1fr; }
  .topbar { align-items: stretch; flex-direction: column; gap: 8px; }
  .top-search { max-width: none; width: 100%; }
  .doc-layout { display: block; padding: 0 12px; }
  .doc-card { padding: 23px 18px 40px; }
  .side-card { margin-top: 15px; position: static; }
}
""".strip() + "\n"


def app_js() -> str:
    return r"""
(() => {
  const DATA = Array.isArray(window.KB_DATA) ? window.KB_DATA : [];
  const PAGE_SIZE = 60;
  const input = document.getElementById('searchInput');
  const collectionFilter = document.getElementById('collectionFilter');
  const categoryFilter = document.getElementById('categoryFilter');
  const yearFilter = document.getElementById('yearFilter');
  const results = document.getElementById('results');
  const resultMeta = document.getElementById('resultMeta');
  const loadMore = document.getElementById('loadMore');
  let visible = PAGE_SIZE;
  let matches = [];
  let debounceTimer;

  const esc = value => String(value || '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const queryTerms = value => value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const yearOf = value => { const match = String(value || '').match(/(?:19|20)\d{2}/); return match ? match[0] : ''; };

  function populate(select, values, current, firstLabel) {
    const sorted = [...new Set(values.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'));
    select.innerHTML = `<option value="">${esc(firstLabel)}</option>` + sorted.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
    if (sorted.includes(current)) select.value = current;
  }

  function refreshCategoryOptions() {
    const current = categoryFilter.value;
    const collection = collectionFilter.value;
    populate(categoryFilter, DATA.filter(item => !collection || item.collection_id === collection).map(item => item.category), current, '全部分类');
  }

  function setCollection(value) {
    collectionFilter.value = value;
    document.querySelectorAll('.collection-card').forEach(card => card.setAttribute('aria-pressed', String(card.dataset.collection === value)));
    refreshCategoryOptions();
    visible = PAGE_SIZE;
    render();
  }

  function score(item, phrase, terms) {
    if (!terms.length) return Number(item.sort_date || 0);
    const title = item.title_search;
    const meta = item.meta_search;
    let total = title.includes(phrase) ? 120 : 0;
    for (const term of terms) {
      if (title.includes(term)) total += 36;
      if (meta.includes(term)) total += 12;
    }
    return total;
  }

  function snippet(item, terms) {
    if (!terms.length) return item.summary || '';
    const text = item.search || item.summary || '';
    let position = -1;
    for (const term of terms) {
      const found = text.indexOf(term);
      if (found >= 0 && (position < 0 || found < position)) position = found;
    }
    if (position < 0) return item.summary || text.slice(0, 190);
    const start = Math.max(0, position - 58);
    return `${start ? '…' : ''}${text.slice(start, start + 230)}${start + 230 < text.length ? '…' : ''}`;
  }

  function highlight(value, terms) {
    let output = esc(value);
    for (const term of terms.slice(0, 5)) {
      if (!term) continue;
      const safe = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      output = output.replace(new RegExp(safe, 'gi'), match => `<mark>${match}</mark>`);
    }
    return output;
  }

  function renderResults(terms) {
    const shown = matches.slice(0, visible);
    if (!shown.length) {
      results.innerHTML = '<div class="empty"><strong>没有找到匹配文档</strong>请减少关键词，或清除分类和年份筛选后重试。</div>';
      loadMore.hidden = true;
      return;
    }
    results.innerHTML = shown.map(({item}) => {
      const meta = [item.agency, item.date, item.status, item.file_no].filter(Boolean).map(value => `<span>${esc(value)}</span>`).join('');
      return `<a class="result" href="${esc(item.page_path)}">
        <div class="result-kicker"><b>${esc(item.collection)}</b><span>${esc(item.category)}</span></div>
        <div class="result-title">${highlight(item.title, terms)}</div>
        <div class="result-meta">${meta}</div>
        <p>${highlight(snippet(item, terms), terms)}</p>
      </a>`;
    }).join('');
    loadMore.hidden = visible >= matches.length;
    loadMore.textContent = `显示更多（剩余 ${Math.max(0, matches.length - visible).toLocaleString()} 条）`;
  }

  function render() {
    const phrase = input.value.trim().toLocaleLowerCase();
    const terms = queryTerms(input.value);
    const collection = collectionFilter.value;
    const category = categoryFilter.value;
    const year = yearFilter.value;
    matches = [];
    for (const item of DATA) {
      if (collection && item.collection_id !== collection) continue;
      if (category && item.category !== category) continue;
      if (year && yearOf(item.date) !== year) continue;
      if (terms.length && !terms.every(term => item.search.includes(term))) continue;
      matches.push({item, score: score(item, phrase, terms)});
    }
    matches.sort((a, b) => b.score - a.score || String(b.item.sort_date).localeCompare(String(a.item.sort_date)) || a.item.title.localeCompare(b.item.title, 'zh-CN'));
    const scope = [collectionFilter.options[collectionFilter.selectedIndex]?.text, categoryFilter.value, yearFilter.value].filter(value => value && !value.startsWith('全部')).join(' · ');
    resultMeta.textContent = `${scope ? scope + '｜' : ''}找到 ${matches.length.toLocaleString()} 篇${terms.length ? '匹配文档' : '文档'}`;
    renderResults(terms);
    const params = new URLSearchParams();
    if (input.value.trim()) params.set('q', input.value.trim());
    if (collection) params.set('collection', collection);
    if (category) params.set('category', category);
    if (year) params.set('year', year);
    const query = params.toString();
    try { history.replaceState(null, '', query ? `?${query}` : location.pathname); } catch (_) {}
  }

  function scheduleRender() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { visible = PAGE_SIZE; render(); }, 180);
  }

  populate(collectionFilter, DATA.map(item => item.collection_id), '', '全部资料库');
  [...collectionFilter.options].forEach(option => {
    const item = DATA.find(entry => entry.collection_id === option.value);
    if (item) option.textContent = item.collection;
  });
  populate(yearFilter, DATA.map(item => yearOf(item.date)), '', '全部年份');
  refreshCategoryOptions();

  const params = new URLSearchParams(location.search);
  input.value = params.get('q') || '';
  if ([...collectionFilter.options].some(option => option.value === params.get('collection'))) collectionFilter.value = params.get('collection');
  refreshCategoryOptions();
  if ([...categoryFilter.options].some(option => option.value === params.get('category'))) categoryFilter.value = params.get('category');
  if ([...yearFilter.options].some(option => option.value === params.get('year'))) yearFilter.value = params.get('year');
  document.querySelectorAll('.collection-card').forEach(card => {
    card.setAttribute('aria-pressed', String(card.dataset.collection === collectionFilter.value));
    card.querySelector('.collection-filter-action').addEventListener('click', () => setCollection(card.getAttribute('aria-pressed') === 'true' ? '' : card.dataset.collection));
  });
  input.addEventListener('input', scheduleRender);
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { clearTimeout(debounceTimer); visible = PAGE_SIZE; render(); } });
  collectionFilter.addEventListener('change', () => setCollection(collectionFilter.value));
  categoryFilter.addEventListener('change', () => { visible = PAGE_SIZE; render(); });
  yearFilter.addEventListener('change', () => { visible = PAGE_SIZE; render(); });
  loadMore.addEventListener('click', () => { visible += PAGE_SIZE; renderResults(queryTerms(input.value)); });
  document.getElementById('resetAll').addEventListener('click', () => {
    input.value = ''; collectionFilter.value = ''; categoryFilter.value = ''; yearFilter.value = '';
    document.querySelectorAll('.collection-card').forEach(card => card.setAttribute('aria-pressed', 'false'));
    refreshCategoryOptions(); visible = PAGE_SIZE; render(); input.focus();
  });
  render();
})();
""".strip() + "\n"


def search_script() -> str:
    return r'''import argparse
import sqlite3
from pathlib import Path


def fts_query(value: str) -> str:
    terms = [term.strip().replace('"', '""') for term in value.split() if len(term.strip()) >= 3]
    return " AND ".join(f'"{term}"' for term in terms)


def main() -> int:
    parser = argparse.ArgumentParser(description="检索金融监管统一知识库")
    parser.add_argument("query", help="关键词；多个词以空格分隔")
    parser.add_argument("--collection", choices=["金融监管制度", "财政部和证监会处罚库", "会计制度"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--db", default=str(Path(__file__).with_name("data") / "knowledge_base.sqlite"))
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    try:
        query = fts_query(args.query)
        filters = []
        params = []
        if args.collection:
            filters.append("d.collection = ?")
            params.append(args.collection)
        if query:
            where = "documents_fts MATCH ?"
            params.insert(0, query)
            if filters:
                where += " AND " + " AND ".join(filters)
            sql = f"""
                SELECT d.collection, d.category, d.title, d.file_no, d.doc_date, d.status,
                       d.html_path, d.markdown_path, d.source_url,
                       snippet(documents_fts, 4, '[', ']', ' … ', 28)
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE {where}
                ORDER BY bm25(documents_fts)
                LIMIT ?
            """
            params.append(args.limit)
        else:
            like = f"%{args.query}%"
            where = "(d.title LIKE ? OR d.metadata LIKE ? OR d.summary LIKE ? OR d.body LIKE ?)"
            params = [like, like, like, like] + params
            if filters:
                where += " AND " + " AND ".join(filters)
            sql = f"""
                SELECT d.collection, d.category, d.title, d.file_no, d.doc_date, d.status,
                       d.html_path, d.markdown_path, d.source_url, substr(d.summary, 1, 220)
                FROM documents d WHERE {where} ORDER BY d.doc_date DESC LIMIT ?
            """
            params.append(args.limit)
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    if not rows:
        print("未找到匹配文档。")
        return 0
    for number, row in enumerate(rows, start=1):
        collection, category, title, file_no, date, status, html_path, markdown_path, url, snippet = row
        print(f"{number}. [{collection} / {category}] {title}")
        print("   " + " | ".join(value for value in [file_no, date, status] if value))
        print(f"   网页：{html_path}")
        print(f"   Markdown：{markdown_path}")
        if url:
            print(f"   来源：{url}")
        if snippet:
            print(f"   {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def localize_system_media(output: Path, media_map: dict[str, str]) -> int:
    localized_pages = 0
    accounting_root = output / "systems" / "accounting"
    if not accounting_root.is_dir() or not media_map:
        return localized_pages
    for page in accounting_root.rglob("*.html"):
        text = page.read_text(encoding="utf-8", errors="replace")
        rewritten = text
        for url, packaged_path in media_map.items():
            media_file = output / "assets" / "media" / Path(packaged_path).name
            relative = os.path.relpath(media_file, page.parent).replace("\\", "/")
            variants = {url}
            if url.startswith("https://"):
                variants.add(url[len("https:") :])
            elif url.startswith("http://"):
                variants.add(url[len("http:") :])
            for variant in variants:
                rewritten = rewritten.replace(f'src="{variant}"', f'src="{relative}"')
        if rewritten != text:
            page.write_text(rewritten, encoding="utf-8")
            localized_pages += 1
    return localized_pages


def copy_system_sites(output: Path, media_map: dict[str, str]) -> dict[str, int]:
    systems_root = output / "systems"
    systems_root.mkdir(parents=True, exist_ok=True)
    counts = {}
    back_control = """
<style>
.unified-hub-link{position:fixed;right:18px;bottom:18px;z-index:9999;background:#0e625c;color:#fff!important;border:1px solid rgba(255,255,255,.45);border-radius:999px;box-shadow:0 8px 24px rgba(15,55,54,.24);font-family:'Microsoft YaHei',sans-serif;font-size:13px;font-weight:700;padding:10px 16px;text-decoration:none!important}
.unified-hub-link:hover{background:#094a46}
@media(max-width:720px){.unified-hub-link{bottom:12px;right:12px;font-size:12px;padding:9px 13px}}
</style>
<a class="unified-hub-link" href="../../打开知识库.html">← 返回统一知识库</a>
""".strip()
    for collection in COLLECTIONS:
        source = collection["site_root"]
        if not source.is_dir():
            raise FileNotFoundError(f"缺少原系统离线站点：{source}")
        destination = systems_root / collection["id"]
        shutil.copytree(source, destination)
        for page in destination.rglob("*.html"):
            text = page.read_text(encoding="utf-8", errors="replace")
            rewritten = re.sub(
                r'<a\b[^>]*href="(?:空缺|无|暂无|不详)"[^>]*>.*?</a>',
                '<span class="source-note">在线来源未提供</span>',
                text,
                flags=re.S,
            )
            if rewritten != text:
                page.write_text(rewritten, encoding="utf-8")
        index_path = destination / "index.html"
        index_text = index_path.read_text(encoding="utf-8")
        index_text, replacements = re.subn(r"(<body(?:\s[^>]*)?>)", r"\1\n" + back_control, index_text, count=1)
        if replacements != 1:
            raise RuntimeError(f"无法向原系统首页加入统一返回入口：{index_path}")
        index_path.write_text(index_text, encoding="utf-8")
        counts[collection["name"]] = sum(1 for path in destination.rglob("*") if path.is_file())
    counts["离线图表页面"] = localize_system_media(output, media_map)
    return counts


def create_sqlite(path: Path, documents: list[dict]) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE documents (
              id INTEGER PRIMARY KEY,
              doc_key TEXT UNIQUE NOT NULL,
              collection TEXT NOT NULL,
              collection_id TEXT NOT NULL,
              category TEXT,
              agency TEXT,
              title TEXT NOT NULL,
              file_no TEXT,
              doc_date TEXT,
              status TEXT,
              tags TEXT,
              metadata TEXT,
              summary TEXT,
              body TEXT,
              source_url TEXT,
              html_path TEXT NOT NULL,
              markdown_path TEXT NOT NULL,
              content_sha256 TEXT NOT NULL
            );
            CREATE INDEX documents_collection_idx ON documents(collection_id, category);
            CREATE INDEX documents_date_idx ON documents(doc_date);
            CREATE INDEX documents_hash_idx ON documents(content_sha256);
            CREATE VIRTUAL TABLE documents_fts USING fts5(
              title, agency, metadata, summary, body,
              content='documents', content_rowid='id', tokenize='trigram'
            );
            """
        )
        rows = []
        for document in documents:
            metadata = " ".join(
                value
                for value in [
                    document["collection"], document["category"], document["agency"], document["file_no"],
                    document["date"], document["status"], " ".join(document["tags"]),
                ]
                if value
            )
            rows.append(
                (
                    document["id"], document["collection"], document["collection_id"], document["category"],
                    document["agency"], document["title"], document["file_no"], document["date"], document["status"],
                    "；".join(document["tags"]), metadata, document["summary"], document["body_text"], document["source_url"],
                    document["page_path"], document["packaged_markdown"], document["content_sha256"],
                )
            )
        connection.executemany(
            """
            INSERT INTO documents
            (doc_key, collection, collection_id, category, agency, title, file_no, doc_date, status, tags,
             metadata, summary, body, source_url, html_path, markdown_path, content_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()


def write_data_files(output: Path, documents: list[dict], manifest: dict) -> None:
    data_dir = output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "index.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for document in documents:
            item = {
                key: document[key]
                for key in [
                    "id", "collection", "collection_id", "category", "agency", "title", "file_no", "date", "status",
                    "tags", "source_url", "page_path", "packaged_markdown", "source_markdown_rel", "summary", "content_sha256",
                ]
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    (data_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    create_sqlite(data_dir / "knowledge_base.sqlite", documents)


def write_search_index(output: Path, documents: list[dict]) -> None:
    items = []
    for document in documents:
        metadata = " ".join(
            value
            for value in [
                document["collection"], document["category"], document["agency"], document["file_no"], document["date"],
                document["status"], " ".join(document["tags"]), document["title"],
            ]
            if value
        ).lower()
        search = clean_text(metadata + " " + document["body_text"]).lower()
        items.append(
            {
                "id": document["id"],
                "collection": document["collection"],
                "collection_id": document["collection_id"],
                "category": document["category"],
                "agency": document["agency"],
                "title": document["title"],
                "title_search": document["title"].lower(),
                "file_no": document["file_no"],
                "date": document["date"],
                "sort_date": re.sub(r"\D", "", document["date"])[:8],
                "status": document["status"],
                "summary": document["summary"],
                "meta_search": metadata,
                "search": search,
                "page_path": document["page_path"],
            }
        )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    (output / "assets" / "search-index.js").write_text("window.KB_DATA=" + payload + ";\n", encoding="utf-8")


def readme_text(manifest: dict) -> str:
    stats = "\n".join(f"- {name}：{count:,} 篇" for name, count in manifest["by_collection"].items())
    return f"""# 金融监管统一知识库（离线包）

生成时间：{manifest['generated_at']}

## 快速使用

1. 双击 `打开知识库.html`。
2. 输入制度名称、文号、机构、条款、当事人、案由或处罚类型。
3. 可按资料库、分类和年份继续筛选。

整个浏览版使用本地 HTML、CSS 和 JavaScript，可直接通过 `file://` 打开，不需要安装服务，也不需要联网。

## 内容统计

- 总文档：{manifest['documents']:,} 篇
{stats}

## 文件说明

- `打开知识库.html` / `index.html`：统一检索入口。
- `systems/`：三套原系统的独立导览与分类页面。
- `docs/`：统一样式的离线正文网页。
- `data/markdown/`：按资料库编号保存的 Markdown 原文。
- `data/index.jsonl`：统一元数据索引。
- `data/knowledge_base.sqlite`：SQLite 全文数据库，使用 FTS5 trigram，支持中文短语检索。
- `search_kb.py`：命令行检索工具。
- `data/manifest.json`：数量、来源和构建报告。

## 命令行检索示例

```powershell
python search_kb.py "内幕交易"
python search_kb.py "资本 管理" --collection "金融监管制度" --limit 30
```

## 口径说明

- 三套来源库均完整保留，精确重复正文不自动删除，避免丢失来源和分类信息。
- 网页入口进行本机全文匹配；SQLite 库适合更精确、批量或二次开发检索。
- 在线来源链接仅用于溯源；离线正文、索引和已缓存图片不依赖这些链接。
"""


def make_manifest(documents: list[dict], media_total: int, media_failures: list[dict]) -> dict:
    hash_counts = Counter(document["content_sha256"] for document in documents)
    duplicate_groups = sum(1 for count in hash_counts.values() if count > 1)
    duplicate_documents = sum(count - 1 for count in hash_counts.values() if count > 1)
    return {
        "name": "金融监管统一知识库",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "documents": len(documents),
        "by_collection": dict(Counter(document["collection"] for document in documents)),
        "by_category": dict(Counter(document["category"] for document in documents)),
        "categories": sorted(set(document["category"] for document in documents)),
        "duplicate_groups": duplicate_groups,
        "duplicate_documents_preserved": duplicate_documents,
        "media": {
            "referenced_external": media_total,
            "cached": media_total - len(media_failures),
            "failed": len(media_failures),
            "failures": media_failures,
        },
        "sources": [
            {
                "id": collection["id"],
                "name": collection["name"],
                "description": collection["description"],
                "navigation": collection["navigation"],
                "portal": f"systems/{collection['id']}/index.html",
            }
            for collection in COLLECTIONS
        ],
    }


def build(
    output: Path, zip_path: Path, skip_media: bool = False, create_zip: bool = True
) -> dict:
    documents = load_documents()
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    (output / "docs").mkdir()
    media_urls = external_image_urls(documents)
    media_map, media_failures = ({}, []) if skip_media else download_media(media_urls, output)
    if skip_media:
        media_failures = [{"url": url, "error": "按参数跳过下载"} for url in sorted(media_urls)]
    url_map, path_map = build_link_maps(documents)
    renderer = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"])
    for document in documents:
        source = document["source_markdown"]
        packaged = output / document["packaged_markdown"]
        packaged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, packaged)
        rewritten = rewrite_markdown(document["markdown_content"], document, url_map, path_map, media_map)
        rendered = renderer.reset().convert(rewritten)
        destination = output / document["page_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document_page(document, rendered), encoding="utf-8")
    manifest = make_manifest(documents, len(media_urls), media_failures)
    (output / "assets" / "site.css").write_text(site_css(), encoding="utf-8")
    (output / "assets" / "app.js").write_text(app_js(), encoding="utf-8")
    write_search_index(output, documents)
    home = index_page(manifest)
    (output / "打开知识库.html").write_text(home, encoding="utf-8")
    (output / "index.html").write_text(home, encoding="utf-8")
    (output / "search_kb.py").write_text(search_script(), encoding="utf-8")
    (output / "README.md").write_text(readme_text(manifest), encoding="utf-8")
    (output / "使用说明.txt").write_text(
        "金融监管统一知识库（离线包）\n\n双击“打开知识库.html”开始使用。\n首页可进入三套原系统导览，也可跨系统统一搜索。\n无需安装，无需联网。\n",
        encoding="utf-8-sig",
    )
    write_data_files(output, documents, manifest)
    result_systems = copy_system_sites(output, media_map)
    if create_zip:
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(output.name) / path.relative_to(output))
    return {
        "output": str(output),
        "zip": str(zip_path) if create_zip else "",
        "documents": len(documents),
        "by_collection": manifest["by_collection"],
        "system_sites": result_systems,
        "media": manifest["media"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合并三套资料库，生成金融监管统一知识库离线包")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--zip", dest="zip_path", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--skip-media", action="store_true", help="跳过外部图片缓存（仅用于快速调试）")
    parser.add_argument("--no-zip", action="store_true", help="只生成源目录，不额外创建离线 ZIP")
    args = parser.parse_args()
    result = build(
        args.output.resolve(), args.zip_path.resolve(), args.skip_media, not args.no_zip
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
