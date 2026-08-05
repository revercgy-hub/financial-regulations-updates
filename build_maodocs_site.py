import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import markdown


ROOT = Path("maodocs_kb")
OUT = Path("maodocs_site")


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
        value = value.strip().strip('"')
        meta[key.strip()] = value
    return meta, text[end + 5 :]


def html_path_for_markdown(markdown_path: str) -> str:
    rel = Path(markdown_path)
    if rel.parts and rel.parts[0] == "markdown":
        rel = Path(*rel.parts[1:])
    return str(rel.with_suffix(".html")).replace("\\", "/")


def site_path_to_html(target: str, root_prefix: str) -> str | None:
    parsed = urlparse(target)
    if parsed.scheme and parsed.netloc != "docs.maoyanqing.com":
        return None
    path = parsed.path if parsed.scheme else target
    anchor = f"#{parsed.fragment}" if parsed.fragment else ""
    if path.startswith("/"):
        path = path[1:]
    if not path:
        return root_prefix + "index.html" + anchor
    if path.endswith("/"):
        path = path + "index.html"
    elif path.endswith(".md"):
        path = path[:-3] + ".html"
    elif not path.endswith(".html"):
        return None
    return root_prefix + path + anchor


def rewrite_markdown_links(text: str, root_prefix: str) -> str:
    def replace_link(match: re.Match) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        wrapped = target.startswith("<") and target.endswith(">")
        if wrapped:
            target = target[1:-1]
        internal_target = site_path_to_html(target, root_prefix)
        if internal_target:
            target = internal_target
        elif target.startswith("/"):
            target = target[1:]
            internal_target = site_path_to_html(target, root_prefix)
            if internal_target:
                target = internal_target
        if target.startswith("markdown/") and target.endswith(".md"):
            target = root_prefix + html_path_for_markdown(target)
        elif target.endswith(".md") and not re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            normalized = re.sub(r"\.md($|#)", r".html\1", target)
            if "/" in normalized and not normalized.startswith(("./", "../")):
                target = root_prefix + normalized
            else:
                target = normalized
        elif target.endswith(".md"):
            target = re.sub(r"\.md($|#)", r".html\1", target)
        if wrapped and re.search(r"[()\\s]", target):
            target = f"<{target}>"
        return f"[{label}]({target})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)


def plain_text(markdown_text: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown_text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_\-|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def page_template(title: str, content: str, source_url: str, home_href: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} | MaoDocs 本地知识库</title>
  <link rel="stylesheet" href="{home_href}assets/site.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="{home_href}index.html">MaoDocs 本地知识库</a>
    <form class="top-search" action="{home_href}index.html">
      <input name="q" placeholder="搜索法规、准则、问答..." autocomplete="off">
      <button type="submit">搜索</button>
    </form>
  </header>
  <main class="doc-layout">
    <article class="doc">
      {content}
    </article>
    <aside class="side">
      <a class="side-link" href="{home_href}index.html">返回首页</a>
      <div class="source-note">来源：{html.escape(source_url)}</div>
    </aside>
  </main>
</body>
</html>
"""


HOME_STRUCTURE = [
    {
        "name": "会计",
        "path": "accounting/index.html",
        "summary": "会计法、企业会计准则、小企业会计准则、政府会计准则制度等。",
        "items": [
            ("会计法", "accounting/al/2024.html"),
            ("企业会计准则", "accounting/ent/index.html"),
            ("企业会计准则应用指南汇编2024", "accounting/ent/casg/index.html"),
            ("企业会计准则解释", "accounting/ent/casi/index.html"),
            ("企业会计准则应用案例", "accounting/ent/casc/index.html"),
            ("企业会计准则实施问答", "accounting/ent/casq/index.html"),
            ("企业财务报表格式", "accounting/ent/fs/index.html"),
            ("会计核算手册", "accounting/ent/am/index.html"),
            ("小企业会计准则", "accounting/se/index.html"),
            ("企业会计制度", "accounting/oe/index.html"),
            ("政府会计准则制度", "accounting/gov/index.html"),
            ("非营利组织及基金类会计制度", "accounting/npo/index.html"),
        ],
    },
    {
        "name": "审计",
        "path": "auditing/index.html",
        "summary": "注册会计师法、职业道德、独立性、执业准则、问题解答和地方协会提示。",
        "items": [
            ("注册会计师法", "auditing/cpal/2026.html"),
            ("中国注册会计师职业道德守则", "auditing/csce/index.html"),
            ("职业道德守则问题解答", "auditing/csceq/index.html"),
            ("中国注册会计师独立性准则", "auditing/csi/index.html"),
            ("独立性准则应用指南", "auditing/csig/index.html"),
            ("中国注册会计师执业准则", "auditing/csa/index.html"),
            ("执业准则应用指南", "auditing/csag/index.html"),
            ("审计准则问题解答", "auditing/csaq/index.html"),
            ("地方注册会计师协会提示", "auditing/lcpa/index.html"),
            ("审计相关其他规定", "auditing/or/index.html"),
        ],
    },
    {
        "name": "证券",
        "path": "securities/index.html",
        "summary": "证券法、交易所规则、监管规则适用指引、信披要求和年报监管报告。",
        "items": [
            ("证券法", "securities/sl/2019.html"),
            ("证券交易所业务规则", "securities/rules/index.html"),
            ("监管规则适用指引", "securities/garr/index.html"),
            ("上市公司监管指引", "securities/rlc/index.html"),
            ("会计监管风险提示", "securities/rwas/index.html"),
            ("信息披露要求", "securities/idcosp/index.html"),
            ("年度财务报告会计监管报告", "securities/asr/index.html"),
            ("上市公司执行准则案例解析", "securities/casca/index.html"),
            ("证券相关其他规定", "securities/or/index.html"),
        ],
    },
    {
        "name": "内控",
        "path": "control/index.html",
        "summary": "企业、小企业、行政事业单位内部控制规范体系及相关讲解、解读和工作底稿指南。",
        "items": [
            ("企业内部控制规范", "control/ent/index.html"),
            ("企业内部控制规范体系", "control/ent/icn/index.html"),
            ("企业内部控制规范讲解2010", "control/ent/icne/index.html"),
            ("企业内部控制规范解读", "control/ent/icni/index.html"),
            ("内部控制审计工作底稿指南", "control/ent/icawp/index.html"),
            ("小企业内部控制规范", "control/se/index.html"),
            ("行政事业单位内部控制规范", "control/api/index.html"),
        ],
    },
    {
        "name": "评估",
        "path": "appraisal/index.html",
        "summary": "资产评估法、资产评估准则、专家指引、操作指引、地方评协提示等。",
        "items": [
            ("资产评估法", "appraisal/aal/2016.html"),
            ("资产评估准则", "appraisal/aas/index.html"),
            ("资产评估专家指引", "appraisal/aaeg/index.html"),
            ("资产评估操作指引", "appraisal/aaog/index.html"),
            ("地方评协相关提示", "appraisal/las/index.html"),
            ("评估相关其他规定", "appraisal/or/index.html"),
        ],
    },
]


def section_count(search_index: list[dict], section_path: str) -> int:
    prefix = section_path.split("/", 1)[0] + "/"
    return sum(1 for item in search_index if item["html_path"].startswith(prefix))


def index_template(search_index: list[dict], stats: dict) -> str:
    data = json.dumps(search_index, ensure_ascii=False, separators=(",", ":"))
    section_blocks = []
    for section in HOME_STRUCTURE:
        links = "\n".join(
            f'<a href="{html.escape(path)}">{html.escape(label)}</a>'
            for label, path in section["items"]
        )
        count = section_count(search_index, section["path"])
        section_blocks.append(
            f"""
    <section class="domain-section" id="{html.escape(section["name"])}">
      <div class="section-head">
        <a class="section-title" href="{html.escape(section["path"])}">{html.escape(section["name"])}</a>
        <span>{count} 篇</span>
      </div>
      <p>{html.escape(section["summary"])}</p>
      <div class="collection-list">{links}</div>
    </section>"""
        )
    section_html = "\n".join(section_blocks)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MaoDocs 本地知识库</title>
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header class="hero">
    <div>
      <h1>审计文库（MaoDocs）</h1>
      <p>注册会计师常用法律法规库，本地静态版，包含 {stats["count"]} 篇页面。</p>
    </div>
    <div class="stat">{stats["size_mb"]} MB</div>
  </header>

  <main class="home">
    <section class="search-panel">
      <input id="searchInput" placeholder="输入关键词，例如：收入确认、独立性、企业会计准则" autofocus>
      <div id="searchMeta" class="search-meta">输入关键词开始搜索</div>
      <div id="results" class="results"></div>
    </section>
    <nav class="quick-nav">
      <a href="#会计">会计</a>
      <a href="#审计">审计</a>
      <a href="#证券">证券</a>
      <a href="#内控">内控</a>
      <a href="#评估">评估</a>
    </nav>
    {section_html}
  </main>

  <script>
  const SEARCH_INDEX = {data};
  const input = document.getElementById('searchInput');
  const results = document.getElementById('results');
  const meta = document.getElementById('searchMeta');

  function tokenize(value) {{
    return value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
  }}

  function score(item, terms) {{
    const title = item.title.toLowerCase();
    const haystack = item.search.toLowerCase();
    let total = 0;
    for (const term of terms) {{
      if (!haystack.includes(term)) return 0;
      total += title.includes(term) ? 20 : 1;
      total += haystack.indexOf(term) >= 0 ? Math.max(1, 10 - Math.floor(haystack.indexOf(term) / 200)) : 0;
    }}
    return total;
  }}

  function snippet(text, terms) {{
    const lower = text.toLowerCase();
    let pos = -1;
    for (const term of terms) {{
      pos = lower.indexOf(term);
      if (pos >= 0) break;
    }}
    if (pos < 0) return text.slice(0, 180);
    const start = Math.max(0, pos - 70);
    return (start ? '...' : '') + text.slice(start, start + 220) + (start + 220 < text.length ? '...' : '');
  }}

  function render() {{
    const terms = tokenize(input.value);
    results.innerHTML = '';
    if (!terms.length) {{
      meta.textContent = '输入关键词开始搜索';
      return;
    }}
    const rows = SEARCH_INDEX
      .map(item => [score(item, terms), item])
      .filter(row => row[0] > 0)
      .sort((a, b) => b[0] - a[0])
      .slice(0, 50)
      .map(row => row[1]);
    meta.textContent = `找到 ${{rows.length}} 条结果（最多显示 50 条）`;
    results.innerHTML = rows.map(item => `
      <a class="result" href="${{item.html_path}}">
        <strong>${{escapeHtml(item.title || item.html_path)}}</strong>
        <span>${{escapeHtml(snippet(item.search, terms))}}</span>
        <small>${{escapeHtml(item.path)}}</small>
      </a>
    `).join('');
  }}

  function escapeHtml(value) {{
    return value.replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
  }}

  input.addEventListener('input', render);
  const q = new URLSearchParams(location.search).get('q');
  if (q) {{
    input.value = q;
    render();
  }}
  </script>
</body>
</html>
"""


def write_css() -> None:
    css = """
:root {
  color-scheme: light;
  --bg: #f6f7f8;
  --panel: #ffffff;
  --text: #182026;
  --muted: #65717b;
  --line: #d9e0e5;
  --accent: #13795b;
  --accent-strong: #0c5b45;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  line-height: 1.75;
}
a { color: var(--accent-strong); text-decoration: none; }
a:hover { text-decoration: underline; }
.hero, .topbar {
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.hero { padding: 36px max(24px, calc((100vw - 1180px) / 2)); }
.hero h1 { margin: 0 0 6px; font-size: 32px; }
.hero p { margin: 0; color: var(--muted); }
.stat { color: var(--accent-strong); font-weight: 700; }
.home { max-width: 1180px; margin: 24px auto 56px; padding: 0 24px; }
.search-panel, .doc, .side {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.search-panel { padding: 18px; margin-bottom: 20px; }
#searchInput, .top-search input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 11px 12px;
  font: inherit;
}
.search-meta { color: var(--muted); font-size: 14px; margin: 10px 0; }
.result {
  display: block;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.result strong, .result span, .result small { display: block; }
.result span { color: var(--text); margin-top: 4px; }
.result small { color: var(--muted); margin-top: 4px; }
.quick-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 18px 0 8px;
}
.quick-nav a {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel);
  color: var(--text);
  padding: 5px 14px;
  font-size: 14px;
}
.domain-section {
  border-top: 1px solid var(--line);
  padding: 28px 0 10px;
}
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 6px;
}
.section-title {
  color: var(--text);
  font-size: 24px;
  font-weight: 750;
}
.section-head span, .domain-section p {
  color: var(--muted);
}
.domain-section p {
  margin: 0 0 14px;
}
.collection-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px 18px;
}
.collection-list a {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--accent-strong);
  overflow: hidden;
  padding: 10px 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 10px max(18px, calc((100vw - 1180px) / 2));
}
.brand { font-weight: 700; color: var(--text); }
.top-search { display: flex; gap: 8px; min-width: 360px; }
.top-search button {
  border: 0;
  border-radius: 6px;
  background: var(--accent);
  color: white;
  padding: 0 16px;
  font: inherit;
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
.doc th, .doc td { border: 1px solid var(--line); padding: 6px 8px; }
.doc code { background: #eef2f4; padding: 2px 4px; border-radius: 4px; }
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
        raise SystemExit("maodocs_kb does not exist. Run crawl_maodocs.py first.")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    write_css()

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
        body = rewrite_markdown_links(body, home_href)
        rendered = md.reset().convert(body)
        destination = OUT / html_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        title = record.get("title") or meta.get("title") or html_path
        destination.write_text(
            page_template(title, rendered, record.get("url", ""), home_href),
            encoding="utf-8",
        )

        text = plain_text(body)
        search_index.append(
            {
                "title": title,
                "path": record["markdown_path"],
                "html_path": html_path,
                "search": (title + " " + record.get("description", "") + " " + text)[:12000],
            }
        )

    total_size = sum(path.stat().st_size for path in OUT.rglob("*") if path.is_file())
    stats = {"count": len(records), "size_mb": round(total_size / 1024 / 1024, 2)}
    (OUT / "index.html").write_text(index_template(search_index, stats), encoding="utf-8")
    (OUT / "site_manifest.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT.resolve()), **stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
