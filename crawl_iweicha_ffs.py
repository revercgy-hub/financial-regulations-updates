import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "http://iweicha.com/smp/smp10.aspx"
SOURCE_URL = "http://iweicha.com/smp/smp10.aspx?sys_code=MS599&def=ffs_page.xml&version=0"
USER_AGENT = "LocalKnowledgeBaseBuilder/1.0 (+personal offline archive)"

TYPES = {
    "1": "金管总局",
    "2": "人行",
    "3": "外汇局",
}

STATE_MAP = {
    "0": "已废止",
    "1": "有效",
    "2": "已修订",
    "3": "将废止",
    "4": "暂缺",
    "5": "无正文",
    "6": "暂缓执行",
}

CLASS_MAP = {
    "01": "管理法",
    "02": "管理办法",
    "03": "办法",
    "04": "细则",
    "05": "规定",
    "06": "指引",
    "07": "要求",
    "08": "意见",
    "09": "决定",
    "10": "批复",
    "11": "通知",
    "12": "通报",
    "13": "公告",
    "20": "其他",
}

LEVEL_MAP = {
    "1": "国家法律",
    "2": "行政法规",
    "3": "部门规章",
    "4": "规范性文件",
    "5": "其他",
}


def request_text(session: requests.Session, url: str, timeout: int = 15) -> str:
    last_error = None
    for attempt in range(1, 3):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def build_url(doc_type: str, fun_type: str, **params: str) -> str:
    query = {
        "sys_code": "MS599",
        "def": "ffs.xml",
        "type": doc_type,
        "fun_type": fun_type,
        "version": "0",
    }
    query.update(params)
    return BASE_URL + "?" + urlencode(query)


def parse_doc_id(href: str) -> str:
    query = parse_qs(urlparse(href.strip()).query, keep_blank_values=True)
    values = query.get("con") or []
    return values[0].strip() if values else ""


def unique_records(records: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for record in records:
        key = (record["type"], record["doc_id"])
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def strip_frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    meta = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def remove_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :]


def scan_existing_records(output_dir: Path) -> list[dict]:
    records = []
    markdown_root = output_dir / "markdown"
    if not markdown_root.exists():
        return records
    for path in markdown_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = strip_frontmatter(text)
        if not meta.get("type") or not meta.get("doc_id"):
            continue
        rel = path.relative_to(output_dir).as_posix()
        meta["markdown_path"] = rel
        meta.setdefault("sha256", hashlib.sha256(text.encode("utf-8")).hexdigest())
        meta.setdefault("text_length", len(text))
        meta.setdefault("line_count", text.count("\n"))
        records.append(meta)
    return records


def discover_type(session: requests.Session, doc_type: str, max_index: int = 80) -> list[dict]:
    url = build_url(doc_type, "1-001", smp_index=str(max_index))
    soup = BeautifulSoup(request_text(session, url, timeout=90), "lxml")
    records = []
    for link in soup.find_all("a"):
        href = link.get("href") or ""
        title = clean_text(link.get_text(" ", strip=True))
        if not title or "fun_type=1-001-1" not in href:
            continue
        doc_id = parse_doc_id(href)
        if not doc_id:
            continue
        records.append(
            {
                "type": doc_type,
                "agency": TYPES[doc_type],
                "doc_id": doc_id,
                "title": title,
                "list_url": urljoin(BASE_URL, href.strip()),
            }
        )
    return unique_records(records)


def clean_text(value: str) -> str:
    value = html.unescape(value or "").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_filename(value: str, max_len: int = 96) -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value[:max_len].rstrip(" .") or "untitled")


def table_to_pairs(soup: BeautifulSoup) -> dict:
    labels = {
        "文件编号（系统）",
        "文件名",
        "年号",
        "文号",
        "文件状态",
        "文件分类",
        "文件等级",
        "废止日期",
        "废止依据",
        "原文链接",
    }
    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in soup.find_all("td")]
    pairs = {}
    index = 0
    while index < len(cells) - 1:
        if cells[index] in labels:
            pairs[cells[index]] = cells[index + 1]
            index += 2
        else:
            index += 1
    return pairs


def content_from_page(soup: BeautifulSoup, title: str) -> tuple[list[str], str]:
    lines = [clean_text(line) for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]
    start = -1
    for marker in ("文件内容", "全文"):
        if marker in lines:
            start = lines.index(marker) + 1
    if start == -1:
        for index, line in enumerate(lines):
            if title and (line == title or title in line or line in title):
                start = index + 1
                break
    if start == -1:
        start = len(lines)
    content_lines = lines[start:]
    stop_markers = {"Copyright © 2012 EOM.NSP.MS. All Rights Reserved."}
    content_lines = [line for line in content_lines if line not in stop_markers]
    nav_markers = {"首页", "目录", "分类", "查询", "返回", "监管文件", "全文", "文件内容"}
    while content_lines and content_lines[0] in nav_markers:
        content_lines.pop(0)
    if content_lines and content_lines[0] == title:
        content_lines.pop(0)
    if len(content_lines) <= 2 and all(line.startswith("(") and line.endswith(")") for line in content_lines):
        content_lines = []
    body = "\n\n".join(content_lines).strip()
    return content_lines, body


def content_is_missing(body_text: str) -> bool:
    text = clean_text(body_text)
    return (
        not text
        or text == "（页面未提供正文或正文暂缺。）"
        or text in {"(有效)", "(已废止)", "(已修订)", "(暂缺)", "(无正文)"}
    )


def metadata_markdown(metadata: dict) -> str:
    rows = [
        ("机构", metadata.get("agency", "")),
        ("文件编号", metadata.get("doc_id", "")),
        ("年号", metadata.get("year", "")),
        ("文号", metadata.get("file_no", "")),
        ("文件状态", metadata.get("state", "")),
        ("文件分类", metadata.get("file_class", "")),
        ("文件等级", metadata.get("level", "")),
        ("废止日期", metadata.get("end_date", "")),
        ("废止依据", metadata.get("end_on", "")),
        ("原文链接", metadata.get("source_link", "")),
    ]
    lines = ["## 发文信息", "", "| 字段 | 内容 |", "| --- | --- |"]
    for key, value in rows:
        lines.append(f"| {key} | {str(value).replace('|', ' ')} |")
    return "\n".join(lines)


def fetch_document(record: dict, output_dir: Path) -> dict:
    session = make_session()
    content_url = build_url(
        record["type"],
        "1-001-2",
        smp_tb_name="ddfiledata",
        fld="file_id",
        con=record["doc_id"],
        smp_index="0",
    )
    fallback_content_url = build_url(
        record["type"],
        "1-001-1",
        form_id="1-001-1",
        smp_index="0",
        fld="file_id",
        con=record["doc_id"],
        smp_type="display_table",
        smp_tb_name="",
    )
    info_url = build_url(
        record["type"],
        "1-001-1-1",
        smp_tb_name="dirdata",
        fld="file_id",
        con=record["doc_id"],
        smp_index="0",
    )
    content_error = None
    try:
        content_html = request_text(session, content_url)
    except Exception as exc:
        content_error = exc
        content_html = ""
    info_html = request_text(session, info_url)
    content_soup = BeautifulSoup(content_html, "lxml")
    info_soup = BeautifulSoup(info_html, "lxml")
    pairs = table_to_pairs(info_soup)

    title = clean_text(pairs.get("文件名") or record["title"])
    content_lines, body_text = content_from_page(content_soup, title)
    if content_is_missing(body_text):
        fallback_html = request_text(session, fallback_content_url)
        fallback_soup = BeautifulSoup(fallback_html, "lxml")
        fallback_lines, fallback_body = content_from_page(fallback_soup, title)
        if not content_is_missing(fallback_body):
            content_html = fallback_html
            content_url = fallback_content_url
            content_soup = fallback_soup
            content_lines = fallback_lines
            body_text = fallback_body
    if content_error and content_is_missing(body_text):
        raise RuntimeError(f"primary and fallback content failed for type={record['type']} doc_id={record['doc_id']}: {content_error}")
    if not pairs.get("文件名") and not body_text:
        raise RuntimeError(f"no document content found for type={record['type']} doc_id={record['doc_id']}")
    year = pairs.get("年号", "")
    state = pairs.get("文件状态", "")
    file_class = pairs.get("文件分类", "")
    level = pairs.get("文件等级", "")
    metadata = {
        "source_url": content_url,
        "info_url": info_url,
        "source_link": pairs.get("原文链接", ""),
        "agency": record["agency"],
        "type": record["type"],
        "doc_id": record["doc_id"],
        "title": title,
        "year": year,
        "file_no": pairs.get("文号", ""),
        "state": state,
        "file_class": file_class,
        "level": level,
        "end_date": pairs.get("废止日期", ""),
        "end_on": pairs.get("废止依据", ""),
    }

    rel_dir = Path("markdown") / record["agency"]
    prefix = f"{record['doc_id'].replace('.', '_')}_"
    name = safe_filename(title)
    rel_path = rel_dir / f"{prefix}{name}.md"
    raw_rel_dir = Path("raw_html") / record["agency"]
    raw_base = f"{record['doc_id'].replace('.', '_')}_{hashlib.sha1(content_url.encode()).hexdigest()[:10]}"

    markdown_lines = ["---"]
    for key, value in metadata.items():
        escaped = str(value).replace('"', '\\"').replace("\n", " ")
        markdown_lines.append(f'{key}: "{escaped}"')
    markdown_lines += ["---", "", f"# {title}", "", metadata_markdown(metadata), "", "## 正文", ""]
    markdown_lines.append(body_text or "（页面未提供正文或正文暂缺。）")
    markdown = "\n".join(markdown_lines).strip() + "\n"

    (output_dir / rel_dir).mkdir(parents=True, exist_ok=True)
    (output_dir / raw_rel_dir).mkdir(parents=True, exist_ok=True)
    (output_dir / rel_path).write_text(markdown, encoding="utf-8")
    (output_dir / raw_rel_dir / f"{raw_base}_content.html").write_text(content_html, encoding="utf-8")
    (output_dir / raw_rel_dir / f"{raw_base}_info.html").write_text(info_html, encoding="utf-8")

    record_out = {
        **metadata,
        "markdown_path": rel_path.as_posix(),
        "raw_content_path": (raw_rel_dir / f"{raw_base}_content.html").as_posix(),
        "raw_info_path": (raw_rel_dir / f"{raw_base}_info.html").as_posix(),
        "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "text_length": len(body_text),
        "line_count": len(content_lines),
    }
    return record_out


def build_sqlite(db_path: Path, records: list[dict]) -> None:
    if db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                agency TEXT,
                doc_id TEXT,
                title TEXT,
                year TEXT,
                file_no TEXT,
                state TEXT,
                file_class TEXT,
                level TEXT,
                source_link TEXT,
                source_url TEXT,
                markdown_path TEXT,
                sha256 TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE documents_fts
            USING fts5(title, agency, year, file_no, state, file_class, body, source_link, markdown_path, tokenize='unicode61')
            """
        )
        for record in records:
            md_path = db_path.parent / record["markdown_path"]
            body = remove_frontmatter(md_path.read_text(encoding="utf-8"))
            cursor = connection.execute(
                """
                INSERT INTO documents
                (agency, doc_id, title, year, file_no, state, file_class, level, source_link, source_url, markdown_path, sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["agency"],
                    record["doc_id"],
                    record["title"],
                    record["year"],
                    record["file_no"],
                    record["state"],
                    record["file_class"],
                    record["level"],
                    record["source_link"],
                    record["source_url"],
                    record["markdown_path"],
                    record["sha256"],
                ),
            )
            connection.execute(
                """
                INSERT INTO documents_fts
                (rowid, title, agency, year, file_no, state, file_class, body, source_link, markdown_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    record["title"],
                    record["agency"],
                    record["year"],
                    record["file_no"],
                    record["state"],
                    record["file_class"],
                    body,
                    record["source_link"],
                    record["markdown_path"],
                ),
            )
        connection.commit()
    finally:
        connection.close()


def write_search_script(output_dir: Path) -> None:
    script = '''import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Search the local iweicha FFS knowledge base.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--db", default=str(Path(__file__).with_name("iweicha_ffs.sqlite")))
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    try:
        rows = connection.execute(
            """
            SELECT d.agency, d.title, d.year, d.state, d.markdown_path,
                   snippet(documents_fts, 6, '[', ']', '...', 18) AS snippet
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            LIMIT ?
            """,
            (args.query, args.limit),
        ).fetchall()
        if not rows:
            like_query = f"%{args.query}%"
            rows = connection.execute(
                """
                SELECT d.agency, d.title, d.year, d.state, d.markdown_path,
                       substr(documents_fts.body, 1, 120) AS snippet
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE d.title LIKE ? OR d.file_no LIKE ? OR d.agency LIKE ? OR d.state LIKE ?
                   OR d.file_class LIKE ? OR documents_fts.body LIKE ?
                LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, like_query, like_query, args.limit),
            ).fetchall()
    finally:
        connection.close()

    for index, row in enumerate(rows, 1):
        agency, title, year, state, path, snippet = row
        print(f"{index}. [{agency}] {title} ({year}, {state})")
        print(f"   {path}")
        print(f"   {snippet}\\n")


if __name__ == "__main__":
    main()
'''
    (output_dir / "search_iweicha_ffs.py").write_text(script, encoding="utf-8")


def write_readme(output_dir: Path, manifest: dict) -> None:
    readme = f"""# iweicha FFS 本地知识库

来源：{SOURCE_URL}

生成时间：{manifest["generated_at"]}

## 内容

- `markdown/`：按机构拆分的 Markdown 正文与发文信息。
- `raw_html/`：抓取时保存的正文页和信息页原始 HTML。
- `iweicha_ffs.sqlite`：SQLite FTS5 全文索引。
- `index.jsonl`：每篇文件一行的元数据索引。
- `manifest.json`：抓取统计。

## 命令行搜索

```powershell
python search_iweicha_ffs.py "资本管理"
python search_iweicha_ffs.py "跨境资金" --limit 20
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local knowledge base from iweicha FFS.")
    parser.add_argument("--output", default="iweicha_ffs_kb")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-index", type=int, default=80)
    parser.add_argument("--limit", type=int, default=0, help="Debug limit per all discovered documents.")
    parser.add_argument("--resume", action="store_true", help="Keep existing output and skip documents already present.")
    parser.add_argument("--offline-finalize", action="store_true", help="Build index, SQLite, README, and manifest from existing Markdown only.")
    parser.add_argument("--batch-size", type=int, default=0, help="Fetch only this many remaining documents, then finalize and exit.")
    parser.add_argument("--refresh-discovery", action="store_true", help="Ignore cached discovered.jsonl and fetch directory pages again.")
    args = parser.parse_args()

    output_dir = Path(args.output)
    if output_dir.exists() and not args.resume:
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = scan_existing_records(output_dir) if args.resume else []
    if args.offline_finalize:
        fetched = sorted(existing, key=lambda item: (item.get("type", ""), natural_doc_id(item.get("doc_id", ""))))
        failures = []
        discovery_path = output_dir / "discovered.jsonl"
        discovered_count = 0
        if discovery_path.exists():
            discovered_count = sum(1 for line in discovery_path.read_text(encoding="utf-8").splitlines() if line.strip())
        write_index(output_dir, fetched)
        build_sqlite(output_dir / "iweicha_ffs.sqlite", fetched)
        manifest = {
            "source": SOURCE_URL,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "documents": len(fetched),
            "failures": len(failures),
            "by_agency": {agency: sum(1 for item in fetched if item.get("agency") == agency) for agency in TYPES.values()},
            "status": "complete" if discovered_count and discovered_count == len(fetched) else "partial_offline_finalize",
        }
        (output_dir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        write_search_script(output_dir)
        write_readme(output_dir, manifest)
        print(json.dumps({"output": str(output_dir.resolve()), **manifest}, ensure_ascii=False, indent=2), flush=True)
        return 0

    discovery_path = output_dir / "discovered.jsonl"
    if discovery_path.exists() and not args.refresh_discovery:
        all_records = [json.loads(line) for line in discovery_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(f"loaded {len(all_records)} discovered documents from cache", flush=True)
    else:
        session = make_session()
        all_records = []
        for doc_type in TYPES:
            records = discover_type(session, doc_type, max_index=args.max_index)
            print(f"discovered {len(records)} documents for {TYPES[doc_type]}", flush=True)
            all_records.extend(records)
        discovery_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in all_records) + "\n",
            encoding="utf-8",
        )
    if args.limit:
        all_records = all_records[: args.limit]

    existing_keys = {(record.get("type"), record.get("doc_id")) for record in existing}
    remaining = [record for record in all_records if (record["type"], record["doc_id"]) not in existing_keys]
    if args.batch_size:
        remaining = remaining[: args.batch_size]
    print(f"existing={len(existing)} remaining={len(remaining)}", flush=True)

    fetched = list(existing)
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {executor.submit(fetch_document, record, output_dir): record for record in remaining}
        for index, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            record = future_map[future]
            try:
                fetched.append(future.result())
            except Exception as exc:
                failures.append({**record, "error": str(exc)})
            if index % 25 == 0:
                write_index(output_dir, fetched)
            if index % 50 == 0 or index == len(remaining):
                print(f"fetched {index}/{len(remaining)} remaining; total={len(fetched)}; failures={len(failures)}", flush=True)

    fetched.sort(key=lambda item: (item["type"], natural_doc_id(item["doc_id"])))
    write_index(output_dir, fetched)
    (output_dir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    build_sqlite(output_dir / "iweicha_ffs.sqlite", fetched)
    manifest = {
        "source": SOURCE_URL,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "documents": len(fetched),
        "failures": len(failures),
        "by_agency": {agency: sum(1 for item in fetched if item["agency"] == agency) for agency in TYPES.values()},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_search_script(output_dir)
    write_readme(output_dir, manifest)
    print(json.dumps({"output": str(output_dir.resolve()), **manifest}, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 1


def write_index(output_dir: Path, records: list[dict]) -> None:
    records = sorted(records, key=lambda item: (item.get("type", ""), natural_doc_id(item.get("doc_id", ""))))
    (output_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def natural_doc_id(doc_id: str) -> tuple:
    parts = []
    for part in re.split(r"(\d+)", doc_id):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part)
    return tuple(parts)


if __name__ == "__main__":
    raise SystemExit(main())
