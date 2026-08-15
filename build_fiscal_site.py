from __future__ import annotations

import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import markdown


ROOT = Path("fiscal_kb")
OUT = Path("fiscal_site")


def strip_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, text[end + 5 :]


def plain_text(value: str) -> str:
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[#>*_`|~-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def html_path(markdown_path: str) -> str:
    path = Path(markdown_path)
    if path.parts and path.parts[0] == "markdown":
        path = Path(*path.parts[1:])
    return path.with_suffix(".html").as_posix()


def page_template(record: dict, rendered: str, home: str) -> str:
    title = html.escape(record.get("title", ""))
    return f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | 财政监管制度库</title><link rel="stylesheet" href="{home}assets/site.css">
</head><body><header class="topbar"><a href="{home}index.html">财政监管制度库</a>
<form action="{home}index.html"><input name="q" placeholder="搜索预算、债务、采购、国库等"><button>搜索</button></form></header>
<main class="doc-layout"><article class="doc">{rendered}</article><aside>
<a class="back" href="{home}index.html">← 返回专题库</a>
<dl><dt>专题</dt><dd>{html.escape(record.get('topic', ''))}</dd><dt>发布机构</dt><dd>{html.escape(record.get('agency', ''))}</dd>
<dt>文号</dt><dd>{html.escape(record.get('file_no', '') or '—')}</dd><dt>发布日期</dt><dd>{html.escape(record.get('publish_date', '') or '—')}</dd></dl>
<a class="source" href="{html.escape(record.get('url', ''))}">查看财政部原文</a></aside></main></body></html>"""


def index_template(records: list[dict], counts: Counter) -> str:
    payload = json.dumps(
        [
            {
                "title": item.get("title", ""),
                "topic": item.get("topic", ""),
                "agency": item.get("agency", ""),
                "file_no": item.get("file_no", ""),
                "date": item.get("publish_date", ""),
                "path": item["html_path"],
                "search": item["search"],
            }
            for item in records
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    chips = "".join(
        f'<button class="chip" data-topic="{html.escape(topic)}">{html.escape(topic)} <strong>{count}</strong></button>'
        for topic, count in counts.items()
    )
    return rf"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>财政监管制度库</title>
<link rel="stylesheet" href="assets/site.css"></head><body>
<section class="hero"><small>FISCAL SUPERVISION · OFFICIAL SOURCES</small><h1>财政监管制度库</h1>
<p>面向财政部监管局工作，覆盖预算、地方债务、财政资金、采购、国库、绩效、行政事业资产和金融企业财政监管。</p>
<strong>{len(records):,}</strong><span> 篇制度 · {len(counts)} 个专题</span></section>
<main class="home"><section class="search"><input id="q" placeholder="输入标题、正文、文号或机构"><button id="go">统一检索</button></section>
<nav><button class="chip active" data-topic="">全部 <strong>{len(records)}</strong></button>{chips}</nav>
<div id="meta">选择专题或输入关键词</div><section id="results" class="results"></section></main>
<script>const DATA={payload};let topic='';const q=document.getElementById('q'),results=document.getElementById('results'),meta=document.getElementById('meta');
const esc=v=>String(v||'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
function render(){{const terms=q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);let rows=DATA.filter(x=>(!topic||x.topic===topic)&&terms.every(t=>x.search.toLowerCase().includes(t)));rows=rows.slice(0,100);meta.textContent=`找到 ${{rows.length}} 条结果（最多显示 100 条）`;results.innerHTML=rows.map(x=>`<a class="result" href="${{esc(x.path)}}"><strong>${{esc(x.title)}}</strong><span>${{esc(x.topic)}} · ${{esc(x.agency)}} · ${{esc(x.date)}}</span><small>${{esc(x.file_no)}}</small></a>`).join('')||'<p class="empty">没有匹配结果，请减少关键词。</p>';}}
document.getElementById('go').onclick=render;q.addEventListener('keydown',e=>{{if(e.key==='Enter')render();}});document.querySelectorAll('.chip').forEach(x=>x.onclick=()=>{{topic=x.dataset.topic;document.querySelectorAll('.chip').forEach(y=>y.classList.toggle('active',y===x));render();}});const initial=new URLSearchParams(location.search).get('q');if(initial)q.value=initial;render();</script></body></html>"""


CSS = """:root{--green:#075f54;--ink:#162226;--muted:#637176;--line:#d8e0de;--bg:#f4f6f5}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.7}a{color:inherit;text-decoration:none}.hero{padding:54px max(24px,calc((100vw - 1120px)/2));background:linear-gradient(135deg,#064e46,#087f70);color:white}.hero small{letter-spacing:.18em;opacity:.78}.hero h1{font-size:42px;line-height:1.2;margin:14px 0}.hero p{max-width:800px;color:#d7eeea}.hero>strong{font:700 44px Georgia,serif}.hero>span{margin-left:8px}.home{max-width:1120px;margin:0 auto;padding:24px}.search,.topbar form{display:flex;gap:10px}.search input,.topbar input{flex:1;border:1px solid var(--line);border-radius:8px;padding:12px 14px;font:inherit}.search button,.topbar button{border:0;border-radius:8px;background:var(--green);color:white;padding:0 20px;font:inherit}nav{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}.chip{border:1px solid var(--line);border-radius:999px;background:white;padding:7px 12px;cursor:pointer}.chip.active{background:var(--green);border-color:var(--green);color:white}.chip strong{margin-left:4px}.results{display:grid;gap:10px;margin-top:12px}.result{display:block;background:white;border:1px solid var(--line);border-radius:10px;padding:14px 16px}.result strong,.result span,.result small{display:block}.result span{color:var(--muted);margin-top:5px}.result small{color:#8a6b24}.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:10px max(20px,calc((100vw - 1120px)/2));background:var(--green);color:white}.topbar>a{font-weight:700}.topbar form{width:min(560px,65vw)}.doc-layout{max-width:1120px;margin:24px auto;padding:0 20px 60px;display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:24px}.doc,aside{background:white;border:1px solid var(--line);border-radius:10px}.doc{padding:30px;overflow-wrap:anywhere}.doc h1{line-height:1.3}.doc table{display:block;overflow:auto;border-collapse:collapse}.doc td,.doc th{border:1px solid var(--line);padding:6px}aside{align-self:start;padding:16px;position:sticky;top:20px}aside dl{font-size:14px}aside dt{color:var(--muted);margin-top:10px}aside dd{margin:0}.back,.source{display:block;color:var(--green);padding:8px 0}.source{border-top:1px solid var(--line);margin-top:12px}@media(max-width:760px){.hero h1{font-size:34px}.topbar,.doc-layout{display:block}.topbar form{width:100%;margin-top:10px}.doc{padding:20px}aside{margin-top:16px;position:static}.search button{padding:0 12px}}"""


def main() -> int:
    if not (ROOT / "index.jsonl").is_file():
        raise SystemExit("fiscal_kb/index.jsonl does not exist")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    records = [json.loads(line) for line in (ROOT / "index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    renderer = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"])
    for record in records:
        raw = (ROOT / record["markdown_path"]).read_text(encoding="utf-8")
        _, body = strip_frontmatter(raw)
        destination_rel = html_path(record["markdown_path"])
        destination = OUT / destination_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        home = "../" * (len(Path(destination_rel).parts) - 1)
        destination.write_text(page_template(record, renderer.reset().convert(body), home), encoding="utf-8", newline="\n")
        record["html_path"] = destination_rel
        record["search"] = " ".join(
            [record.get("title", ""), record.get("topic", ""), record.get("agency", ""), record.get("file_no", ""), plain_text(body)[:16000]]
        )
    counts = Counter(record.get("topic", "未分类") for record in records)
    (OUT / "index.html").write_text(index_template(records, counts), encoding="utf-8", newline="\n")
    (OUT / "assets" / "site.css").write_text(CSS + "\n", encoding="utf-8", newline="\n")
    manifest = {"documents": len(records), "by_topic": dict(counts)}
    (OUT / "site_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT.resolve()), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
