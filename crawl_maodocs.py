import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import html2text
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://docs.maoyanqing.com/"
SITEMAP_URL = urljoin(BASE_URL, "sitemap.xml")
USER_AGENT = "LocalKnowledgeBaseBuilder/1.0 (+personal offline archive)"


def safe_rel_path(url: str, suffix: str) -> Path:
    parsed = urlparse(url)
    path = parsed.path
    raw_path = path.strip("/")
    if not raw_path:
        raw_path = "index"
    elif path.endswith("/"):
        raw_path = raw_path.rstrip("/") + "/index"
    elif raw_path.endswith(".html"):
        raw_path = raw_path[:-5]
    return Path(raw_path + suffix)


def fetch_text(session: requests.Session, url: str, timeout: int = 30) -> str:
    last_error = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content.decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def parse_sitemap(xml_text: str) -> list[dict]:
    root = ElementTree.fromstring(xml_text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    pages = []
    for url_node in root.findall("sm:url", namespace):
        loc = url_node.findtext("sm:loc", default="", namespaces=namespace)
        if not loc:
            continue
        lastmod = url_node.findtext("sm:lastmod", default="", namespaces=namespace)
        if urlparse(loc).netloc == urlparse(BASE_URL).netloc:
            pages.append({"url": loc, "lastmod": lastmod})
    return pages


def configure_converter() -> html2text.HTML2Text:
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = False
    converter.ignore_emphasis = False
    converter.ignore_links = False
    converter.protect_links = True
    converter.unicode_snob = True
    converter.mark_code = True
    converter.wrap_links = False
    return converter


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.replace("\xa0", " ")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    return markdown.strip() + "\n"


def rewrite_local_links(soup: BeautifulSoup, current_url: str) -> None:
    for tag in soup.find_all(["a", "img"]):
        attr = "href" if tag.name == "a" else "src"
        value = tag.get(attr)
        if not value or value.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(current_url, value)
        parsed = urlparse(absolute)
        if parsed.netloc == urlparse(BASE_URL).netloc and parsed.path.endswith(".html"):
            tag[attr] = "/" + safe_rel_path(absolute, ".md").as_posix()
        else:
            tag[attr] = absolute


def extract_page(url: str, html: str, converter: html2text.HTML2Text) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.split("|", 1)[0].strip()
    meta_description = ""
    description_tag = soup.find("meta", attrs={"name": "description"})
    if description_tag:
        meta_description = (description_tag.get("content") or "").strip()

    content = soup.find(id="markdown-content")
    if not content or not content.get_text(strip=True):
        content = soup.find("main", id="main-content") or soup.find("main")

    if content:
        for removable in content.select(
            "script, style, nav, footer, .vp-page-meta, .vp-toc, .vp-breadcrumb, .vp-page-nav"
        ):
            removable.decompose()
        rewrite_local_links(content, url)
        body_html = "".join(str(child) for child in content.children)
        body_markdown = normalize_markdown(converter.handle(body_html))
        body_text = content.get_text("\n", strip=True)
    else:
        body_markdown = ""
        body_text = ""

    frontmatter = {
        "source_url": url,
        "title": title,
        "description": meta_description,
    }
    frontmatter_lines = ["---"]
    for key, value in frontmatter.items():
        escaped = str(value).replace('"', '\\"')
        frontmatter_lines.append(f'{key}: "{escaped}"')
    frontmatter_lines.append("---")
    markdown = "\n".join(frontmatter_lines) + "\n\n"
    if title and not body_markdown.lstrip().startswith("#"):
        markdown += f"# {title}\n\n"
    markdown += body_markdown

    return {
        "title": title,
        "description": meta_description,
        "markdown": markdown,
        "text": body_text,
    }


def build_sqlite(db_path: Path, records: list[dict]) -> None:
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, url TEXT UNIQUE, path TEXT, title TEXT, description TEXT, lastmod TEXT, sha256 TEXT)"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(title, description, body, url, path, tokenize='unicode61')"
        )
        for record in records:
            cursor = connection.execute(
                "INSERT INTO documents (url, path, title, description, lastmod, sha256) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["url"],
                    record["markdown_path"],
                    record["title"],
                    record["description"],
                    record["lastmod"],
                    record["sha256"],
                ),
            )
            doc_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO documents_fts (rowid, title, description, body, url, path) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    record["title"],
                    record["description"],
                    record["text"],
                    record["url"],
                    record["markdown_path"],
                ),
            )
        connection.commit()
    finally:
        connection.close()


def write_readme(output_dir: Path, manifest: dict) -> None:
    readme = f"""# MaoDocs 本地知识库

来源：{BASE_URL}

生成时间：{manifest["generated_at"]}

## 内容

- `markdown/`：清洗后的 Markdown 正文，适合 Obsidian、VS Code、RAG 入库或全文检索。
- `raw_html/`：从站点抓取的原始 HTML 页面，便于核验。
- `maodocs.sqlite`：SQLite FTS5 全文索引。
- `index.jsonl`：每页一行的元数据索引。
- `manifest.json`：本次抓取统计。

## 检索示例

```powershell
python search_maodocs.py "收入确认"
python search_maodocs.py "注册会计师 独立性" --limit 20
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")


def write_search_script(output_dir: Path) -> None:
    script = '''import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Search the local MaoDocs knowledge base.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--db", default=str(Path(__file__).with_name("maodocs.sqlite")))
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    try:
        rows = connection.execute(
            """
            SELECT d.title, d.path, d.url, snippet(documents_fts, 2, '[', ']', ' ... ', 18)
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY bm25(documents_fts)
            LIMIT ?
            """,
            (args.query, args.limit),
        ).fetchall()
    finally:
        connection.close()

    for index, (title, path, url, snippet) in enumerate(rows, start=1):
        print(f"{index}. {title}")
        print(f"   {path}")
        print(f"   {url}")
        if snippet:
            print(f"   {snippet}")
        print()


if __name__ == "__main__":
    main()
'''
    (output_dir / "search_maodocs.py").write_text(script, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror docs.maoyanqing.com into a local Markdown knowledge base.")
    parser.add_argument("--output", default="maodocs_kb")
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--limit", type=int, default=0, help="For testing only; 0 means all sitemap URLs.")
    args = parser.parse_args()

    output_dir = Path(args.output)
    markdown_dir = output_dir / "markdown"
    raw_dir = output_dir / "raw_html"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    sitemap_text = fetch_text(session, SITEMAP_URL)
    (output_dir / "sitemap.xml").write_text(sitemap_text, encoding="utf-8")
    pages = parse_sitemap(sitemap_text)
    if args.limit:
        pages = pages[: args.limit]

    converter = configure_converter()
    records = []
    failures = []

    total = len(pages)
    for index, page in enumerate(pages, start=1):
        url = page["url"]
        raw_path = raw_dir / safe_rel_path(url, ".html")
        markdown_path = markdown_dir / safe_rel_path(url, ".md")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            html = fetch_text(session, url)
            raw_path.write_text(html, encoding="utf-8")
            extracted = extract_page(url, html, converter)
            markdown_path.write_text(extracted["markdown"], encoding="utf-8")
            sha256 = hashlib.sha256(extracted["markdown"].encode("utf-8")).hexdigest()
            records.append(
                {
                    "url": url,
                    "lastmod": page.get("lastmod", ""),
                    "title": extracted["title"],
                    "description": extracted["description"],
                    "text": extracted["text"],
                    "markdown_path": str(markdown_path.relative_to(output_dir)).replace("\\", "/"),
                    "raw_html_path": str(raw_path.relative_to(output_dir)).replace("\\", "/"),
                    "sha256": sha256,
                    "bytes": len(extracted["markdown"].encode("utf-8")),
                }
            )
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})

        if index % 50 == 0 or index == total:
            print(f"[{index}/{total}] ok={len(records)} failed={len(failures)}", flush=True)
        if args.delay:
            time.sleep(args.delay)

    index_path = output_dir / "index.jsonl"
    with index_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps({k: v for k, v in record.items() if k != "text"}, ensure_ascii=False) + "\n")

    build_sqlite(output_dir / "maodocs.sqlite", records)

    manifest = {
        "source": BASE_URL,
        "sitemap": SITEMAP_URL,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "total_sitemap_urls": len(pages),
        "downloaded_pages": len(records),
        "failed_pages": len(failures),
        "output_dir": str(output_dir.resolve()),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    write_readme(output_dir, manifest)
    write_search_script(output_dir)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if failures:
        print(f"Failures written to {output_dir / 'failures.json'}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
