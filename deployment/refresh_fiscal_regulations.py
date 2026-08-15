"""Incrementally maintain an official fiscal-supervision regulation library.

The library is deliberately separate from financial-sector regulation and
accounting standards.  Every record is assigned to one operational topic used
by MOF local regulatory bureaux.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crawl_maodocs as maodocs


ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "fiscal_kb"
INDEX = KB / "index.jsonl"
BASE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
ARTICLE_PATTERN = re.compile(r"/20\d{4}/t20\d{6}_\d+\.htm$")
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 250


TOPICS = {
    "预算管理": {
        "agency": "财政部预算司",
        "list_urls": ("https://yss.mof.gov.cn/zhengceguizhang/",),
        "include": r"预算|财政体制|财政事权|收支分类|一般性支出|三公经费|项目库|结转结余",
        "exclude": r"债务|债券|转移支付|绩效",
        "seeds": (
            "https://yss.mof.gov.cn/zhengceguizhang/200805/t20080522_33648.htm",
            "https://yss.mof.gov.cn/zhengceguizhang/202210/t20221008_3844654.htm",
        ),
    },
    "地方政府债务": {
        "agency": "财政部预算司",
        "list_urls": ("https://yss.mof.gov.cn/zhuantilanmu/dfzgl/zcfg/",),
        "include": r"债务|债券|举债|融资平台|风险",
        "exclude": "",
        "seeds": (
            "https://yss.mof.gov.cn/zhengceguizhang/201512/t20151223_1627720.htm",
            "https://yss.mof.gov.cn/zhuantilanmu/dfzgl/zcfg/201701/t20170125_2527827.htm",
        ),
    },
    "转移支付与财政资金": {
        "agency": "财政部预算司",
        "list_urls": ("https://yss.mof.gov.cn/zhengceguizhang/",),
        "include": r"转移支付|直达资金|共同财政事权|奖补资金|财力保障|财政资金",
        "exclude": "",
        "seeds": (
            "https://yss.mof.gov.cn/zhengceguizhang/201205/t20120504_648716.htm",
            "https://yss.mof.gov.cn/zhengceguizhang/201608/t20160825_2401925.htm",
            "https://yss.mof.gov.cn/zhengceguizhang/201805/t20180503_2884937.htm",
        ),
    },
    "政府采购": {
        "agency": "财政部国库司（政府采购管理办公室）",
        "list_urls": (
            "https://www.ccgp.gov.cn/zcfg/mof/",
            "https://www.ccgp.gov.cn/zcfg/mofgz/",
        ),
        "include": r"采购|政府购买服务|代理机构|评审专家|质疑|投诉",
        "exclude": "",
        "seeds": (
            "https://www.ccgp.gov.cn/zcfg/mof/202601/t20260122_26102526.htm",
        ),
    },
    "国库与预算执行": {
        "agency": "财政部国库司",
        "list_urls": ("https://gks.mof.gov.cn/guizhangzhidu/",),
        "include": r"国库|预算执行|集中支付|财政专户|公务卡|政府财务报告|决算|非税收入",
        "exclude": "",
        "seeds": (
            "https://gks.mof.gov.cn/guizhangzhidu/202002/t20200210_3467524.htm",
            "https://gks.mof.gov.cn/ztztz/guokujizhongzhifuguanli_1/200806/t20080618_46188.htm",
        ),
    },
    "预算绩效管理": {
        "agency": "财政部预算司",
        "list_urls": ("https://yss.mof.gov.cn/zhengceguizhang/",),
        "include": r"绩效管理|绩效评价|绩效目标|绩效监控|绩效指标",
        "exclude": "",
        "seeds": (
            "https://yss.mof.gov.cn/zhengceguizhang/201811/t20181116_3070316.htm",
            "https://yss.mof.gov.cn/zhengceguizhang/201107/t20110718_577332.htm",
        ),
    },
    "行政事业性资产与财务": {
        "agency": "财政部资产管理司",
        "list_urls": ("https://zcgls.mof.gov.cn/zhengcefabu/",),
        "include": r"行政事业|国有资产|资产管理|资产配置|资产处置|资产使用|资产报告|财务规则",
        "exclude": "",
        "seeds": (
            "https://zcgls.mof.gov.cn/zhengcefabu/201901/t20190110_3120776.htm",
            "https://zcgls.mof.gov.cn/zhengcefabu/202105/t20210507_3697666.htm",
            "https://zcgls.mof.gov.cn/zhengcefabu/202409/t20240913_3943766.htm",
        ),
    },
    "金融企业财政监管": {
        "agency": "财政部金融司",
        "list_urls": (
            "https://jrs.mof.gov.cn/zhengcefabu/",
            "https://jrs.mof.gov.cn/gongzuotongzhi/",
        ),
        "include": r"金融企业|国有金融|财务规则|绩效评价|准备金|财务快报|资产管理公司|融资担保",
        "exclude": r"招聘|会议|培训",
        "seeds": (
            "https://jrs.mof.gov.cn/gongzuodongtai/200901/t20090123_110612.htm",
            "https://jrs.mof.gov.cn/zhengcefabu/cwjx/202006/t20200602_3524593.htm",
            "https://jrs.mof.gov.cn/zhengcefabu/cwjx/202006/t20200602_3524578.htm",
            "https://jrs.mof.gov.cn/gongzuotongzhi/202301/t20230110_3862592.htm",
        ),
    },
}


def clean_text(value: str) -> str:
    value = (value or "").replace("\xa0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def canonical_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "", ""))


def request_text(session: requests.Session, url: str, attempts: int = 8) -> str:
    last_error = None
    for attempt in range(1, attempts + 1):
        target = url
        if attempt > 1:
            target += ("&" if "?" in target else "?") + f"finreg_retry={time.time_ns()}_{attempt}"
        try:
            response = session.get(target, timeout=45)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            text = response.text
            if "502 Bad Gateway" in text or ("Bad Gateway" in text and len(text) < 5000):
                raise RuntimeError("official gateway returned an error page")
            return text
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(min(attempt, 3))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def paged_url(url: str, page: int) -> str:
    if page == 0:
        return urljoin(url, "index.htm")
    return urljoin(url, f"index_{page}.htm")


def matches_topic(topic: str, text: str) -> bool:
    config = TOPICS[topic]
    include = str(config["include"])
    exclude = str(config["exclude"])
    return bool(re.search(include, text)) and not (exclude and re.search(exclude, text))


def discover_topic(session: requests.Session, topic: str, pages: int) -> list[dict]:
    config = TOPICS[topic]
    found: dict[str, dict] = {}
    for list_url in config["list_urls"]:
        for page in range(pages):
            url = paged_url(str(list_url), page)
            try:
                source = request_text(session, url)
            except Exception as error:
                print(f"Fiscal list skipped: {url} ({error})")
                continue
            soup = BeautifulSoup(source, "lxml")
            count = 0
            for link in soup.select("a[href]"):
                target = canonical_url(urljoin(url, link.get("href", "")))
                parsed = urlparse(target)
                if parsed.scheme != "https" or not ARTICLE_PATTERN.search(parsed.path):
                    continue
                title = clean_text(link.get_text(" ", strip=True))
                if len(title) < 5 or not matches_topic(topic, title):
                    continue
                parent = clean_text(link.parent.get_text(" ", strip=True))
                date_match = re.search(r"20\d{2}[-年./]\d{1,2}[-月./]\d{1,2}日?", parent)
                found[target] = {
                    "url": target,
                    "title": title,
                    "publish_date": date_match.group(0) if date_match else "",
                    "topic": topic,
                    "agency": config["agency"],
                }
                count += 1
            print(f"Fiscal {topic} page {page + 1}: discovered {count}")
            time.sleep(0.25)
    for seed in config["seeds"]:
        target = canonical_url(str(seed))
        found.setdefault(
            target,
            {"url": target, "title": "", "publish_date": "", "topic": topic, "agency": config["agency"]},
        )
    return list(found.values())


def extract_file_no(text: str) -> str:
    patterns = (
        r"(?:财预|财库|财资|财金|财监|财办预|财政部令)\s*[〔\[]?\s*20\d{2}\s*[〕\]]?\s*第?\d+\s*号",
        r"(?:国务院令|国办发|国发)\s*第?\s*\d+\s*号",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(0))
    return ""


def extract_pdf(session: requests.Session, url: str) -> str:
    try:
        response = session.get(url, timeout=90)
        response.raise_for_status()
        if len(response.content) > MAX_PDF_BYTES:
            raise RuntimeError("PDF exceeds size limit")
        reader = PdfReader(io.BytesIO(response.content))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise RuntimeError("PDF exceeds page limit")
        return clean_text("\n\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception as error:
        print(f"Fiscal PDF text skipped: {url} ({error})")
        return ""


def article_body(soup: BeautifulSoup):
    return (
        soup.select_one(".TRS_Editor")
        or soup.select_one(".article_con")
        or soup.select_one("#zoom")
        or soup.select_one(".vF_detail_content")
        or soup.find(class_=re.compile(r"article.*content|content.*article|Custom_UnionStyle", re.I))
        or soup.find("body")
    )


def stable_paths(url: str, topic: str) -> tuple[Path, Path]:
    parsed = urlparse(url)
    match = re.search(r"/(20\d{4})/(t20\d{6}_\d+)\.htm$", parsed.path)
    if not match:
        raise RuntimeError(f"unexpected official article URL: {url}")
    year_month, identifier = match.groups()
    host = parsed.netloc.replace(".", "-")
    topic_slug = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:8]
    base = Path(topic_slug) / host / year_month / identifier
    return Path("markdown") / base.with_suffix(".md"), Path("raw_html") / base.with_suffix(".html")


def extract_article(session: requests.Session, listing: dict) -> dict:
    source = request_text(session, listing["url"])
    soup = BeautifulSoup(source, "lxml")
    content = article_body(soup)
    if content is None:
        raise RuntimeError("official article body was not found")
    for removable in content.select("script, style, noscript, nav"):
        removable.decompose()
    maodocs.rewrite_local_links(content, listing["url"])
    body = maodocs.normalize_markdown(maodocs.configure_converter().handle(str(content)))
    visible = clean_text(content.get_text("\n", strip=True))
    title_node = soup.find(["h1", "h2"])
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else listing.get("title", "")
    if len(title) < 5 and soup.title:
        title = clean_text(re.split(r"[_|-]", soup.title.get_text(" ", strip=True), maxsplit=1)[0])
    if len(title) < 5:
        title = listing.get("title", "")
    if len(title) < 5:
        raise RuntimeError("official article title was not found")
    page_text = clean_text(soup.get_text("\n", strip=True))
    date_match = re.search(r"(?:发布日期|发布时间)[：:]?\s*(20\d{2}年\d{1,2}月\d{1,2}日)", page_text)
    publish_date = date_match.group(1) if date_match else listing.get("publish_date", "")
    if not publish_date:
        url_date = re.search(r"/t(20\d{2})(\d{2})(\d{2})_", urlparse(listing["url"]).path)
        if url_date:
            publish_date = f"{url_date.group(1)}年{int(url_date.group(2))}月{int(url_date.group(3))}日"
    file_no = extract_file_no(title + "\n" + visible[:1600])
    attachments = []
    seen = set()
    for link in content.select("a[href]"):
        target = canonical_url(urljoin(listing["url"], link.get("href", "")))
        if not urlparse(target).path.lower().endswith(".pdf") or target in seen:
            continue
        seen.add(target)
        label = clean_text(link.get_text(" ", strip=True)) or Path(urlparse(target).path).name
        extracted = extract_pdf(session, target)
        block = f"## 附件：{label}\n\n[查看财政部原附件]({target})"
        if extracted:
            block += "\n\n" + extracted
        attachments.append(block)
    frontmatter = {
        "source_url": listing["url"],
        "source": "财政部官方渠道",
        "topic": listing["topic"],
        "agency": listing["agency"],
        "title": title,
        "file_no": file_no,
        "publish_date": publish_date,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f'{key}: "{str(value).replace(chr(34), chr(92) + chr(34))}"')
    lines.extend(["---", "", f"# {title}", "", body])
    if attachments:
        lines.extend(["", "\n\n".join(attachments)])
    markdown = maodocs.normalize_markdown("\n".join(lines))
    markdown_path, raw_path = stable_paths(listing["url"], listing["topic"])
    return {
        "url": listing["url"],
        "title": title,
        "topic": listing["topic"],
        "category": listing["topic"],
        "agency": listing["agency"],
        "source": "财政部官方渠道",
        "file_no": file_no,
        "publish_date": publish_date,
        "lastmod": publish_date,
        "description": f"{listing['agency']} · {listing['topic']}",
        "markdown_path": markdown_path.as_posix(),
        "raw_html_path": raw_path.as_posix(),
        "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "bytes": len(markdown.encode("utf-8")),
        "attachment_pdfs": len(seen),
        "markdown": markdown,
        "raw_html": source,
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_record(record: dict) -> dict:
    markdown = record.pop("markdown")
    raw_html = record.pop("raw_html")
    markdown_path = KB / record["markdown_path"]
    raw_path = KB / record["raw_html_path"]
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
    raw_path.write_text(raw_html, encoding="utf-8", newline="\n")
    return record


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh official fiscal-supervision topics.")
    parser.add_argument("--topics", default=",".join(TOPICS), help="comma-separated topic names or 'all'")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--max-new-per-topic", type=int, default=20)
    parser.add_argument("--refresh-existing", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.pages <= 10:
        raise SystemExit("--pages must be between 1 and 10")
    requested = set(TOPICS) if args.topics == "all" else {item.strip() for item in args.topics.split(",") if item.strip()}
    if not requested or requested - set(TOPICS):
        raise SystemExit(f"invalid --topics: {args.topics}")

    session = requests.Session()
    session.headers.update({"User-Agent": BASE_USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"})
    existing = load_jsonl(INDEX)
    existing_by_key = {(item.get("topic"), item.get("url")): item for item in existing}
    retained = [item for item in existing if item.get("topic") not in requested]
    refreshed = []
    total_fetched = total_reused = total_skipped = 0
    for topic in TOPICS:
        if topic not in requested:
            continue
        discovered = discover_topic(session, topic, args.pages)
        if not discovered:
            cached = [item for item in existing if item.get("topic") == topic]
            if cached:
                refreshed.extend(cached)
                print(f"Fiscal {topic}: discovery unavailable; retained {len(cached)} cached records")
                continue
            raise RuntimeError(f"no records discovered for required topic: {topic}")
        topic_rows = []
        new_count = 0
        for listing in discovered:
            previous = existing_by_key.get((topic, listing["url"]))
            if previous and not args.refresh_existing:
                topic_rows.append(previous)
                total_reused += 1
                continue
            if not previous and args.max_new_per_topic and new_count >= args.max_new_per_topic:
                continue
            try:
                topic_rows.append(write_record(extract_article(session, listing)))
                total_fetched += 1
                if not previous:
                    new_count += 1
            except Exception as error:
                if previous:
                    topic_rows.append(previous)
                    total_reused += 1
                    print(f"Fiscal cached article retained: {listing['url']} ({error})")
                else:
                    total_skipped += 1
                    print(f"Fiscal new article skipped: {listing['url']} ({error})")
            time.sleep(0.25)
        old_topic = [item for item in existing if item.get("topic") == topic]
        known = {(item.get("topic"), item.get("url")) for item in topic_rows}
        topic_rows.extend(item for item in old_topic if (item.get("topic"), item.get("url")) not in known)
        if not topic_rows:
            raise RuntimeError(f"topic refresh produced no usable records: {topic}")
        refreshed.extend(topic_rows)

    merged = retained + refreshed
    sequence_by_key = {}
    next_sequence = 1
    for item in existing:
        key = (item.get("topic"), item.get("url"))
        sequence = int(item.get("sequence") or next_sequence)
        sequence_by_key[key] = sequence
        next_sequence = max(next_sequence, sequence + 1)
    for item in merged:
        key = (item.get("topic"), item.get("url"))
        if key not in sequence_by_key:
            sequence_by_key[key] = next_sequence
            next_sequence += 1
        item["sequence"] = sequence_by_key[key]
    merged.sort(key=lambda item: int(item["sequence"]))
    write_jsonl(INDEX, merged)
    counts = dict(Counter(item.get("topic", "未分类") for item in merged))
    manifest = {
        "schema": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "documents": len(merged),
        "by_topic": counts,
        "official_only": True,
        "topics": list(TOPICS),
    }
    (KB / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Fiscal update: {len(merged)} records; fetched {total_fetched}, reused {total_reused}, skipped {total_skipped}; {counts}")


if __name__ == "__main__":
    main()
