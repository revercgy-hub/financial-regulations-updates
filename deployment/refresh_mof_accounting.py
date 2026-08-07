"""Incrementally merge official MOF Accounting Department publications into accounting KB."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import markdown as markdown_lib
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crawl_maodocs as maodocs


ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "maodocs_kb"
OFFICIAL_INDEX = KB / "mof_accounting_index.jsonl"
BASE_URL = "https://kjs.mof.gov.cn/"
SOURCE = "财政部会计司"
CHANNELS = (
    ("政策发布", "zhengcefabu", True),
    ("工作通知", "gongzuotongzhi", True),
    ("政策解读", "zhengcejiedu", True),
)
FALLBACK_RECENT = {
    "zhengcefabu": (
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202608/t20260805_3994927.htm",
            "关于印发《企业会计准则第30号——财务报表列报》的通知",
            "2026年08月05日",
        ),
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202606/t20260630_3992536.htm",
            "关于印发《管理会计应用指引第804号——财务共享服务》的通知",
            "2026年07月01日",
        ),
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202606/t20260615_3991686.htm",
            "关于印发《企业会计准则解释第20号》的通知",
            "2026年06月17日",
        ),
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202601/t20260127_3982643.htm",
            "关于印发《可持续信息鉴证业务准则 第6101号——基本准则（试行）》的通知",
            "2026年01月27日",
        ),
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202601/t20260112_3981685.htm",
            "关于印发《〈社会保险基金会计制度〉补充规定》的通知",
            "2026年01月12日",
        ),
        (
            "https://kjs.mof.gov.cn/zhengcefabu/202512/t20251225_3980202.htm",
            "关于印发《企业可持续披露准则第1号——气候（试行）》的通知",
            "2025年12月25日",
        ),
    )
}
INCLUDE_PATTERN = re.compile(
    r"会计|审计|准则|制度|内部控制|内控|财务报告|财会监督|注册会计师|"
    r"可持续披露|信息化|职业道德|管理会计|资产评估|实施问答|应用案例"
)
EXCLUDE_PATTERN = re.compile(
    r"考试|成绩|合格标准|资格评审|人才评价|培训班|知识竞赛|招聘|表彰|名单|会议通知"
)
ARTICLE_PATTERN = re.compile(r"/(?:[a-z]+)/20\d{4}/t20\d{6}_\d+\.htm$")
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 250


def clean_text(value: str) -> str:
    value = (value or "").replace("\xa0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def request_text(
    session: requests.Session,
    url: str,
    timeout: int = 45,
    attempts: int = 4,
    cache_bust: bool = True,
) -> str:
    last_error = None
    for attempt in range(1, attempts + 1):
        target = url
        if cache_bust and attempt > 1:
            separator = "&" if "?" in url else "?"
            target = f"{url}{separator}finreg_retry={time.time_ns()}_{attempt}"
        try:
            response = session.get(target, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(min(attempt, 3))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def page_url(slug: str, page: int) -> str:
    name = "index.htm" if page == 0 else f"index_{page}.htm"
    return urljoin(BASE_URL, f"{slug}/{name}")


def discover_channel(
    session: requests.Session, channel: str, slug: str, filtered: bool, pages: int
) -> list[dict]:
    records = []
    seen = set()
    for page in range(pages):
        url = page_url(slug, page)
        try:
            html = request_text(session, url, timeout=30, attempts=8)
        except Exception as error:
            print(f"MOF accounting list failed: {url} ({error})")
            continue
        soup = BeautifulSoup(html, "lxml")
        found = 0
        for link in soup.find_all("a", href=True):
            target = urljoin(url, link["href"])
            parsed = urlparse(target)
            if parsed.netloc != "kjs.mof.gov.cn" or not ARTICLE_PATTERN.search(parsed.path):
                continue
            title = clean_text(link.get_text(" ", strip=True))
            if len(title) < 5 or target in seen:
                continue
            if filtered and (not INCLUDE_PATTERN.search(title) or EXCLUDE_PATTERN.search(title)):
                continue
            parent_text = clean_text(link.parent.get_text(" ", strip=True))
            date_match = re.search(r"20\d{2}[-年./]\d{1,2}[-月./]\d{1,2}日?", parent_text)
            publish_date = date_match.group(0) if date_match else ""
            records.append(
                {
                    "url": target,
                    "title": title,
                    "publish_date": publish_date,
                    "channel": channel,
                    "slug": slug,
                }
            )
            seen.add(target)
            found += 1
        print(f"MOF accounting {channel} page {page + 1}: discovered {found}")
        time.sleep(0.5)
    return records


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def stable_paths(record: dict) -> tuple[Path, Path]:
    parsed = urlparse(record["url"])
    match = re.search(r"/(20\d{4})/(t20\d{6}_\d+)\.htm$", parsed.path)
    if not match:
        raise RuntimeError(f"Unexpected MOF Accounting Department URL: {record['url']}")
    year_month, identifier = match.groups()
    base = Path("accounting") / "mof-kjs" / record["slug"] / year_month / identifier
    return Path("markdown") / base.with_suffix(".md"), Path("raw_html") / base.with_suffix(".html")


def extract_file_no(text: str) -> str:
    match = re.search(
        r"(?:财(?:办)?会|财资|财库|财监|会协|中注协)\s*[〔\[]\s*20\d{2}\s*[〕\]]\s*\d+\s*号",
        text,
    )
    return re.sub(r"\s+", "", match.group(0)) if match else ""


def pdf_text(session: requests.Session, url: str) -> str:
    last_error = None
    for attempt in range(1, 3):
        try:
            response = session.get(url, timeout=90)
            response.raise_for_status()
            if len(response.content) > MAX_PDF_BYTES:
                raise RuntimeError("PDF attachment exceeds size limit")
            reader = PdfReader(io.BytesIO(response.content))
            if len(reader.pages) > MAX_PDF_PAGES:
                raise RuntimeError("PDF attachment exceeds page limit")
            return clean_text("\n\n".join(page.extract_text() or "" for page in reader.pages))
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(attempt)
    print(f"MOF accounting PDF text skipped: {url} ({last_error})")
    return ""


def extract_article(session: requests.Session, listing: dict) -> dict:
    html = request_text(session, listing["url"], timeout=45, attempts=8)
    soup = BeautifulSoup(html, "lxml")
    content = (
        soup.select_one(".TRS_Editor")
        or soup.select_one(".article_con")
        or soup.select_one("#zoom")
        or soup.find(class_=re.compile(r"article.*content|content.*article|Custom_UnionStyle", re.I))
    )
    if content is None:
        raise RuntimeError("official article body was not found")
    for removable in content.select("script, style, noscript, nav"):
        removable.decompose()
    maodocs.rewrite_local_links(content, listing["url"])
    converter = maodocs.configure_converter()
    body = maodocs.normalize_markdown(converter.handle(str(content)))
    page_text = clean_text(content.get_text("\n", strip=True))
    title_node = soup.find(["h1", "h2"])
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else listing["title"]
    if len(title) < 5:
        title = listing["title"]
    full_text = clean_text(soup.get_text("\n", strip=True))
    date_match = re.search(r"发布日期[：:]?\s*(20\d{2}年\d{1,2}月\d{1,2}日)", full_text)
    publish_date = date_match.group(1) if date_match else listing.get("publish_date", "")
    file_no = extract_file_no(
        title + ("\n" + page_text[:800] if listing["channel"] == "政策发布" else "")
    )

    attachments = []
    seen_attachments = set()
    for link in soup.find_all("a", href=True):
        target = urljoin(listing["url"], link["href"])
        if not urlparse(target).path.lower().endswith(".pdf") or target in seen_attachments:
            continue
        seen_attachments.add(target)
        label = clean_text(link.get_text(" ", strip=True)) or Path(urlparse(target).path).name
        extracted = pdf_text(session, target)
        block = f"## 附件：{label}\n\n[查看财政部原附件]({target})\n"
        if extracted:
            block += "\n" + extracted + "\n"
        attachments.append(block)

    frontmatter = {
        "source_url": listing["url"],
        "source": SOURCE,
        "channel": listing["channel"],
        "title": title,
        "file_no": file_no,
        "publish_date": publish_date,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        escaped = str(value).replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.extend(["---", "", f"# {title}", "", body])
    if attachments:
        lines.extend(["", "\n".join(attachments)])
    markdown = maodocs.normalize_markdown("\n".join(lines))
    markdown_path, raw_path = stable_paths(listing)
    return {
        "url": listing["url"],
        "lastmod": publish_date,
        "title": title,
        "description": f"{SOURCE} · {listing['channel']}",
        "markdown_path": markdown_path.as_posix(),
        "raw_html_path": raw_path.as_posix(),
        "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "bytes": len(markdown.encode("utf-8")),
        "source": SOURCE,
        "agency": SOURCE,
        "channel": listing["channel"],
        "file_no": file_no,
        "publish_date": publish_date,
        "attachment_pdfs": len(seen_attachments),
        "markdown": markdown,
        "raw_html": html,
    }


def write_record(record: dict) -> dict:
    markdown_path = KB / record["markdown_path"]
    raw_path = KB / record["raw_html_path"]
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(record.pop("markdown"), encoding="utf-8", newline="\n")
    raw_path.write_text(record.pop("raw_html"), encoding="utf-8", newline="\n")
    return record


def markdown_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end >= 0:
            raw = raw[end + 5 :]
    return clean_text(BeautifulSoup(markdown_lib.markdown(raw), "lxml").get_text("\n"))


def rebuild_sqlite(records: list[dict]) -> None:
    enriched = []
    for record in records:
        item = dict(record)
        item["text"] = markdown_text(KB / item["markdown_path"])
        enriched.append(item)
    maodocs.build_sqlite(KB / "maodocs.sqlite", enriched)


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=2, help="recent pages per official channel")
    parser.add_argument("--max-new", type=int, default=0, help="testing limit; 0 fetches every new item")
    parser.add_argument(
        "--max-new-per-channel",
        type=int,
        default=0,
        help="production safety limit per channel; 0 fetches every new item",
    )
    parser.add_argument("--refresh-existing", action="store_true")
    parser.add_argument(
        "--channels",
        default=",".join(slug for _, slug, _ in CHANNELS),
        help="comma-separated official channel slugs",
    )
    args = parser.parse_args()
    if args.pages < 1 or args.pages > 10:
        raise SystemExit("--pages must be between 1 and 10")
    requested_channels = {item.strip() for item in args.channels.split(",") if item.strip()}
    known_channels = {slug for _, slug, _ in CHANNELS}
    if not requested_channels or requested_channels - known_channels:
        raise SystemExit(f"Invalid --channels value: {args.channels}")
    if not (KB / "index.jsonl").is_file():
        raise RuntimeError("maodocs_kb must be restored or built first")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_URL,
        }
    )
    cached = load_jsonl(OFFICIAL_INDEX)
    cached_by_url = {record["url"]: record for record in cached}
    discovered = []
    for channel, slug, filtered in CHANNELS:
        if slug not in requested_channels:
            continue
        channel_records = discover_channel(session, channel, slug, filtered, args.pages)
        if not channel_records and slug in FALLBACK_RECENT:
            channel_records = [
                {
                    "url": url,
                    "title": title,
                    "publish_date": publish_date,
                    "channel": channel,
                    "slug": slug,
                }
                for url, title, publish_date in FALLBACK_RECENT[slug]
            ]
            print(f"MOF accounting {channel}: using {len(channel_records)} recent URL seeds")
        discovered.extend(channel_records)
    if not discovered and not cached:
        raise RuntimeError("No MOF Accounting Department records were discovered and no cache exists")

    refreshed_by_url = dict(cached_by_url)
    reused = fetched = skipped = 0
    fetched_by_channel: dict[str, int] = {}
    for listing in discovered:
        previous = cached_by_url.get(listing["url"])
        if previous and previous.get("title") == listing["title"] and not args.refresh_existing:
            reused += 1
            continue
        if args.max_new and fetched >= args.max_new:
            continue
        channel_fetched = fetched_by_channel.get(listing["channel"], 0)
        if (
            not previous
            and args.max_new_per_channel
            and channel_fetched >= args.max_new_per_channel
        ):
            continue
        try:
            refreshed_by_url[listing["url"]] = write_record(extract_article(session, listing))
            fetched += 1
            fetched_by_channel[listing["channel"]] = channel_fetched + 1
        except Exception as error:
            if previous:
                reused += 1
                print(f"MOF accounting kept cached article: {listing['url']} ({error})")
            else:
                skipped += 1
                print(f"MOF accounting skipped new article: {listing['url']} ({error})")
        time.sleep(0.5)

    official = list(cached)
    known = {record["url"] for record in official}
    for listing in discovered:
        record = refreshed_by_url.get(listing["url"])
        if record and record["url"] not in known:
            official.append(record)
            known.add(record["url"])
    official = [refreshed_by_url.get(record["url"], record) for record in official]
    write_jsonl(OFFICIAL_INDEX, official)

    current = load_jsonl(KB / "index.jsonl")
    base = [record for record in current if record.get("source") != SOURCE]
    merged = base + official
    write_jsonl(KB / "index.jsonl", merged)
    rebuild_sqlite(merged)

    manifest_path = KB / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "total_documents": len(merged),
            "official_mof_accounting_documents": len(official),
            "official_mof_accounting_updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        f"MOF accounting update: {len(official)} official records; "
        f"reused {reused}, fetched {fetched}, skipped {skipped}; total accounting {len(merged)}"
    )


if __name__ == "__main__":
    main()
