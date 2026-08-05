import html
import json
import re
import shutil
from pathlib import Path

import markdown


ROOT = Path("iweicha_ffs_kb")
OUT = Path("iweicha_ffs_site")


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


def html_path_for_markdown(markdown_path: str) -> str:
    rel = Path(markdown_path)
    if rel.parts and rel.parts[0] == "markdown":
        rel = Path(*rel.parts[1:])
    return str(rel.with_suffix(".html")).replace("\\", "/")


def plain_text(markdown_text: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown_text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def body_only(markdown_text: str) -> str:
    if "\n## 正文\n" in markdown_text:
        return markdown_text.split("\n## 正文\n", 1)[1]
    if "## 正文" in markdown_text:
        return markdown_text.split("## 正文", 1)[1]
    return markdown_text


def rewrite_markdown_links(text: str) -> str:
    def repl(match: re.Match) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        if target.startswith("http://") or target.startswith("https://") or target.startswith("#"):
            return match.group(0)
        if target.endswith(".md"):
            target = target[:-3] + ".html"
        return f"[{label}]({target})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, text)


def page_template(title: str, content: str, meta: dict, home_href: str) -> str:
    source_link = meta.get("source_link", "")
    source_html = (
        f'<a class="side-link" href="{html.escape(source_link)}" target="_blank" rel="noreferrer">原文链接</a>'
        if source_link
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} | FFS 本地知识库</title>
  <link rel="stylesheet" href="{home_href}assets/site.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="{home_href}index.html">FFS 本地知识库</a>
    <form class="top-search" action="{home_href}index.html">
      <input name="q" placeholder="搜索监管文件、文号、条款" autocomplete="off">
      <button type="submit">搜索</button>
    </form>
  </header>
  <main class="doc-layout">
    <article class="doc">
      {content}
    </article>
    <aside class="side">
      <a class="side-link" href="{home_href}index.html">返回首页</a>
      {source_html}
      <div class="source-note">
        <strong>{html.escape(meta.get("agency", ""))}</strong><br>
        {html.escape(meta.get("year", ""))} · {html.escape(meta.get("state", ""))} · {html.escape(meta.get("file_class", ""))}
      </div>
    </aside>
  </main>
</body>
</html>
"""


def index_template(search_index: list[dict], manifest: dict) -> str:
    data = json.dumps(search_index, ensure_ascii=False, separators=(",", ":"))
    agency_counts = manifest.get("by_agency", {})
    agency_cards = "\n".join(
        f"""
        <button class="agency-filter" type="button" data-agency="{html.escape(agency)}">
          <span>{html.escape(agency)}</span><strong>{count}</strong>
        </button>"""
        for agency, count in agency_counts.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FFS 本地知识库</title>
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header class="hero">
    <div>
      <h1>金融监管文件本地知识库</h1>
      <p>来自 iweicha FFS，已离线整理为 {manifest.get("documents", 0)} 篇 Markdown 文件，可全文搜索、按机构浏览。</p>
    </div>
    <div class="stat">{manifest.get("documents", 0)} 篇</div>
  </header>
  <main class="home">
    <section class="search-panel">
      <div class="search-row">
        <input id="searchInput" placeholder="输入关键词，例如：资本管理、支付结算、跨境资金、银监发〔2007〕" autofocus>
        <button id="clearFilter" type="button">全部</button>
      </div>
      <div class="agency-grid">{agency_cards}</div>
      <div id="searchMeta" class="search-meta">输入关键词开始搜索</div>
      <div id="results" class="results"></div>
    </section>
  </main>
  <script>
  const DATA = {data};
  const input = document.getElementById('searchInput');
  const results = document.getElementById('results');
  const meta = document.getElementById('searchMeta');
  const clearFilter = document.getElementById('clearFilter');
  let activeAgency = '';

  function escapeHtml(value) {{
    return value.replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
  }}

  function snippet(text, terms) {{
    const lower = text.toLowerCase();
    let pos = -1;
    for (const term of terms) {{
      const found = lower.indexOf(term.toLowerCase());
      if (found !== -1 && (pos === -1 || found < pos)) pos = found;
    }}
    if (pos === -1) return text.slice(0, 120);
    const start = Math.max(0, pos - 36);
    return (start ? '...' : '') + text.slice(start, start + 150) + (start + 150 < text.length ? '...' : '');
  }}

  function metaChip(value) {{
    return value ? `<span>${{escapeHtml(value)}}</span>` : '';
  }}

  function render() {{
    const query = input.value.trim();
    const terms = query ? query.split(/\\s+/).filter(Boolean) : [];
    let matches = DATA;
    if (activeAgency) matches = matches.filter(item => item.agency === activeAgency);
    if (terms.length) {{
      matches = matches.filter(item => {{
        const haystack = item.search.toLowerCase();
        return terms.every(term => haystack.includes(term.toLowerCase()));
      }});
    }}
    matches = matches.slice(0, 80);
    const label = activeAgency ? activeAgency + ' · ' : '';
    meta.textContent = terms.length || activeAgency ? `${{label}}找到 ${{matches.length}} 条结果` : '输入关键词开始搜索';
    results.innerHTML = matches.map(item => `
      <a class="result" href="${{item.html_path}}">
        <div class="result-title">${{escapeHtml(item.title)}}</div>
        <div class="result-meta">
          ${{metaChip(item.agency)}}
          ${{metaChip(item.year)}}
          ${{metaChip(item.state)}}
          ${{metaChip(item.file_no ? '文号 ' + item.file_no : '')}}
        </div>
        <p>${{escapeHtml(snippet(item.summary || item.search, terms))}}</p>
      </a>
    `).join('');
  }}

  input.addEventListener('input', render);
  clearFilter.addEventListener('click', () => {{
    activeAgency = '';
    document.querySelectorAll('.agency-filter').forEach(button => button.classList.remove('active'));
    render();
  }});
  document.querySelectorAll('.agency-filter').forEach(button => {{
    button.addEventListener('click', () => {{
      activeAgency = button.dataset.agency;
      document.querySelectorAll('.agency-filter').forEach(item => item.classList.toggle('active', item === button));
      render();
    }});
  }});
  const q = new URLSearchParams(location.search).get('q');
  if (q) input.value = q;
  render();
  </script>
</body>
</html>
"""


def write_css() -> None:
    css = """
:root {
  --bg: #f7f7f3;
  --panel: #ffffff;
  --text: #1e2428;
  --muted: #667077;
  --line: #d9ded8;
  --accent: #176b63;
  --accent-strong: #0f514b;
  --gold: #a77622;
  --red: #8f3e32;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.72 "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
}
a { color: var(--accent-strong); text-decoration: none; }
.hero {
  min-height: 240px;
  padding: 42px max(24px, calc((100vw - 1180px) / 2));
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  background:
    linear-gradient(125deg, rgba(23, 107, 99, .92), rgba(22, 42, 52, .88)),
    url("https://images.unsplash.com/photo-1450101499163-c8848c66ca85?auto=format&fit=crop&w=1800&q=80");
  background-size: cover;
  background-position: center;
  color: white;
}
.hero h1 {
  margin: 0 0 12px;
  font-size: clamp(32px, 5vw, 56px);
  line-height: 1.08;
  letter-spacing: 0;
}
.hero p { max-width: 760px; margin: 0; color: rgba(255,255,255,.86); }
.stat {
  border: 1px solid rgba(255,255,255,.45);
  padding: 12px 18px;
  font-size: 24px;
  font-weight: 700;
  white-space: nowrap;
}
.home { max-width: 1180px; margin: -28px auto 60px; padding: 0 24px; }
.search-panel, .doc, .side {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 12px 30px rgba(34, 44, 48, .08);
}
.search-panel { padding: 18px; }
.search-row, .top-search { display: flex; gap: 10px; }
#searchInput, .top-search input {
  width: 100%;
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0 12px;
  font: inherit;
}
button {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  padding: 0 14px;
  font: inherit;
  cursor: pointer;
}
.agency-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.agency-filter {
  justify-content: space-between;
  display: flex;
  align-items: center;
}
.agency-filter.active {
  border-color: var(--accent);
  color: var(--accent-strong);
  background: #eef6f4;
}
.search-meta { color: var(--muted); font-size: 14px; margin: 14px 0; }
.results { display: grid; gap: 10px; }
.result {
  display: block;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 12px 14px;
  background: #fff;
}
.result:hover { border-color: var(--accent); }
.result-title {
  color: var(--accent-strong);
  display: -webkit-box;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.result-meta span {
  background: #eef3f1;
  border: 1px solid #dbe6e2;
  border-radius: 999px;
  color: #536168;
  font-size: 12px;
  line-height: 1;
  padding: 5px 8px;
}
.result p {
  color: #3d454a;
  display: -webkit-box;
  font-size: 14px;
  line-height: 1.55;
  margin: 8px 0 0;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 10px max(18px, calc((100vw - 1180px) / 2));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  background: rgba(247, 247, 243, .94);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(10px);
}
.brand { color: var(--text); font-weight: 700; white-space: nowrap; }
.top-search { min-width: 380px; }
.top-search button {
  border: 0;
  background: var(--accent);
  color: white;
}
.doc-layout {
  max-width: 1180px;
  margin: 24px auto 56px;
  padding: 0 24px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 20px;
}
.doc { padding: 28px; overflow-wrap: anywhere; }
.doc h1 { line-height: 1.25; margin-top: 0; }
.doc h2, .doc h3 { line-height: 1.35; margin-top: 30px; }
.doc table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }
.doc th, .doc td { border: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
.side { align-self: start; padding: 14px; position: sticky; top: 74px; }
.side-link { display: block; padding: 8px 0; }
.source-note {
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  margin-top: 10px;
  overflow-wrap: anywhere;
  padding-top: 12px;
}
@media (max-width: 820px) {
  .hero, .topbar { align-items: stretch; flex-direction: column; }
  .top-search { min-width: 0; width: 100%; }
  .doc-layout { display: block; }
  .side { margin-top: 16px; position: static; }
}
"""
    assets = OUT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "site.css").write_text(css.strip() + "\n", encoding="utf-8")


def main() -> int:
    if not ROOT.exists():
        raise SystemExit("iweicha_ffs_kb does not exist. Run crawl_iweicha_ffs.py first.")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    write_css()

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (ROOT / "index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"])
    search_index = []
    for record in records:
        source_md = ROOT / record["markdown_path"]
        raw = source_md.read_text(encoding="utf-8")
        meta, body = strip_frontmatter(raw)
        html_path = html_path_for_markdown(record["markdown_path"])
        depth = len(Path(html_path).parts) - 1
        home_href = "../" * depth
        body = rewrite_markdown_links(body)
        rendered = md.reset().convert(body)
        destination = OUT / html_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        title = record.get("title") or meta.get("title") or html_path
        destination.write_text(page_template(title, rendered, {**meta, **record}, home_href), encoding="utf-8")
        summary = plain_text(body_only(body))
        text = plain_text(body)
        search_index.append(
            {
                "title": title,
                "agency": record.get("agency", ""),
                "year": record.get("year", ""),
                "state": record.get("state", ""),
                "file_no": record.get("file_no", ""),
                "html_path": html_path,
                "summary": summary[:3000],
                "search": (title + " " + record.get("agency", "") + " " + record.get("file_no", "") + " " + text)[:16000],
            }
        )

    (OUT / "index.html").write_text(index_template(search_index, manifest), encoding="utf-8")
    (OUT / "site_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT.resolve()), "documents": len(records)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
