#!/usr/bin/env python3
"""
md_to_html.py — Markdown 仕様書を完全オフラインの HTML へ変換する。

system-spec-writer が生成する docs/ 配下の Markdown(index.md + 各仕様書.md)を
入力とし、仕様書ごとに 1 つの HTML を出力する。index.md は各ドキュメントへの
ポータル(サイドバー付き)になる。

特徴:
  - 外部ライブラリ非依存(Python 標準ライブラリのみ)
  - Mermaid 図をレンダリング(mermaid.min.js を同梱し assets/ にコピー)
  - GitHub Alerts(> [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION])を
    色分けされたコールアウトに変換
  - .md 同士の内部リンクを .html へ自動で張り替え
  - GFM テーブル・チェックリスト・fenced code・見出し・リスト・引用に対応
  - ネット接続なしでブラウザで開ける(mermaid はローカル参照)

使い方:
  python3 md_to_html.py <入力ディレクトリ or 単一.md> <出力ディレクトリ> [オプション]

オプション:
  --title "サイト名"   サイドバー見出しに使うタイトル(省略時は入力から推測)
  --standalone         mermaid.min.js を各 HTML に埋め込み、1ファイル単体で完結させる
                       (ファイルは大きくなるが assets/ 無しでどこでも開ける)

このスクリプトは同梱の ../assets/mermaid.min.js を使う。
"""

import argparse
import html
import os
import re
import sys
import shutil

# ---- GitHub Alerts の定義 ---------------------------------------------------
ALERTS = {
    "NOTE":      ("補足", "note"),
    "TIP":       ("ヒント", "tip"),
    "IMPORTANT": ("重要", "important"),
    "WARNING":   ("注意", "warning"),
    "CAUTION":   ("警告", "caution"),
}

# ---- インライン記法 ---------------------------------------------------------

def _rewrite_href(url: str) -> str:
    """.md への相対リンクを .html に張り替える(外部URL/アンカーは触らない)。"""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url) or url.startswith("#") or url.startswith("mailto:"):
        return url
    # #アンカーを分離
    anchor = ""
    if "#" in url:
        url, anchor = url.split("#", 1)
        anchor = "#" + anchor
    if url.endswith(".md"):
        url = url[:-3] + ".html"
    return url + anchor


def inline(text: str) -> str:
    """段落・セル・リスト項目などのインライン Markdown を HTML に変換する。"""
    # 1. インラインコードを退避(中身は後で二重処理させない)
    code_spans = []

    def stash_code(m):
        code_spans.append("<code>" + html.escape(m.group(1)) + "</code>")
        return f"\x00C{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash_code, text)

    # 2. HTML エスケープ
    text = html.escape(text, quote=False)

    # 3. リンク [text](url)
    def link(m):
        label, url = m.group(1), m.group(2).strip()
        return f'<a href="{html.escape(_rewrite_href(url), quote=True)}">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)

    # 4. 強調(太字 → 斜体の順)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*(?!\s)([^*]+?)\*(?![\*\w])", r"<em>\1</em>", text)

    # 5. コードを戻す
    for i, c in enumerate(code_spans):
        text = text.replace(f"\x00C{i}\x00", c)
    return text


# ---- ブロック解析 -----------------------------------------------------------

FENCE_RE = re.compile(r"^(```+|~~~+)[ \t]*([\w-]*)[ \t]*$")


def slugify(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text)          # タグ除去
    s = re.sub(r"[^\w぀-ヿ一-鿿 -]", "", s).strip()
    s = re.sub(r"\s+", "-", s)
    return s.lower() or "section"


def convert(md: str, toc: list) -> str:
    """1 ファイル分の Markdown 本文を HTML に変換する。toc に (level, text, id) を追記。"""
    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    seen_ids = {}

    def uniq_id(base: str) -> str:
        seen_ids[base] = seen_ids.get(base, 0) + 1
        return base if seen_ids[base] == 1 else f"{base}-{seen_ids[base]}"

    while i < n:
        line = lines[i]

        # --- fenced code / mermaid ---
        fm = FENCE_RE.match(line.strip())
        if fm:
            fence, lang = fm.group(1)[0], fm.group(2).lower()
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence * 3):
                body.append(lines[i])
                i += 1
            i += 1  # 終端フェンスを飛ばす
            content = "\n".join(body)
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(content)}</pre>')
            else:
                label = f'<span class="code-lang">{html.escape(lang)}</span>' if lang else ""
                out.append(
                    f'<div class="codeblock">{label}'
                    f'<pre><code>{html.escape(content)}</code></pre></div>'
                )
            continue

        # --- 空行 ---
        if line.strip() == "":
            i += 1
            continue

        # --- 見出し ---
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            level = len(hm.group(1))
            txt = inline(hm.group(2).strip())
            hid = uniq_id(slugify(hm.group(2).strip()))
            toc.append((level, hm.group(2).strip(), hid))
            out.append(f'<h{level} id="{hid}">{txt}</h{level}>')
            i += 1
            continue

        # --- 水平線 ---
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        # --- テーブル(GFM) ---
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            header = lines[i]
            sep = lines[i + 1]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(lines[i])
                i += 1
            out.append(render_table(header, sep, rows))
            continue

        # --- 引用 / GitHub Alerts ---
        if line.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(render_quote(quote, toc))
            continue

        # --- リスト(順序なし / 順序あり / チェックボックス) ---
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            block = []
            while i < n and (re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]) or
                             (lines[i].strip() and lines[i].startswith((" ", "\t")))):
                block.append(lines[i])
                i += 1
            out.append(render_list(block))
            continue

        # --- 段落(空行/ブロック開始まで) ---
        para = []
        while i < n and lines[i].strip() != "" and not _is_block_start(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")

    return "\n".join(out)


def _is_block_start(lines, i) -> bool:
    line = lines[i]
    if FENCE_RE.match(line.strip()):
        return True
    if re.match(r"^#{1,6}\s", line):
        return True
    if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line.strip()):
        return True
    if line.lstrip().startswith(">"):
        return True
    if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
        return True
    if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
        return True
    return False


def render_table(header, sep, rows) -> str:
    def cells(row):
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [c.strip() for c in row.split("|")]

    aligns = []
    for c in cells(sep):
        c = c.strip()
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        elif c.startswith(":"):
            aligns.append("left")
        else:
            aligns.append("")

    def style(idx):
        a = aligns[idx] if idx < len(aligns) else ""
        return f' style="text-align:{a}"' if a else ""

    head = cells(header)
    html_rows = ["<thead><tr>" +
                 "".join(f"<th{style(j)}>{inline(c)}</th>" for j, c in enumerate(head)) +
                 "</tr></thead>"]
    body = []
    for r in rows:
        cs = cells(r)
        body.append("<tr>" +
                    "".join(f"<td{style(j)}>{inline(c)}</td>" for j, c in enumerate(cs)) +
                    "</tr>")
    html_rows.append("<tbody>" + "".join(body) + "</tbody>")
    return '<div class="table-wrap"><table>' + "".join(html_rows) + "</table></div>"


def render_quote(quote_lines, toc) -> str:
    # 先頭が [!TYPE] なら Alerts コールアウト
    first = quote_lines[0].strip() if quote_lines else ""
    m = re.match(r"^\[!(\w+)\]\s*$", first)
    if m and m.group(1).upper() in ALERTS:
        title, cls = ALERTS[m.group(1).upper()]
        inner = convert("\n".join(quote_lines[1:]).strip(), toc)
        return (f'<div class="admonition admonition-{cls}">'
                f'<p class="admonition-title">{title}</p>{inner}</div>')
    inner = convert("\n".join(quote_lines).strip(), toc)
    return f"<blockquote>{inner}</blockquote>"


def render_list(block) -> str:
    """インデントでネストを判定してリストを構築する。"""
    items = []  # (indent, ordered, checked_or_None, text)
    for line in block:
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            indent = len(m.group(1).replace("\t", "    "))
            ordered = bool(re.match(r"\d+\.", m.group(2)))
            text = m.group(3)
            checked = None
            cm = re.match(r"^\[([ xX])\]\s+(.*)$", text)
            if cm:
                checked = cm.group(1).lower() == "x"
                text = cm.group(2)
            items.append([indent, ordered, checked, text])
        elif items and line.strip():
            # 継続行 → 直前の項目に連結
            items[-1][3] += " " + line.strip()

    def build(idx, cur_indent):
        html_out = []
        ordered = items[idx][1]
        tag = "ol" if ordered else "ul"
        html_out.append(f"<{tag}>")
        while idx < len(items):
            indent, o, checked, text = items[idx]
            if indent < cur_indent:
                break
            if indent > cur_indent:
                sub, idx = build(idx, indent)
                # 直前の <li> に子リストを差し込む
                if html_out and html_out[-1].endswith("</li>"):
                    html_out[-1] = html_out[-1][:-5] + sub + "</li>"
                else:
                    html_out.append(sub)
                continue
            if checked is not None:
                box = "☑" if checked else "☐"
                cls = " class=\"task done\"" if checked else " class=\"task\""
                html_out.append(f'<li{cls}><span class="checkbox">{box}</span> {inline(text)}</li>')
            else:
                html_out.append(f"<li>{inline(text)}</li>")
            idx += 1
        html_out.append(f"</{tag}>")
        return "".join(html_out), idx

    if not items:
        return ""
    result, _ = build(0, items[0][0])
    return result


# ---- ページ生成 -------------------------------------------------------------

def page_title(md: str, fallback: str) -> str:
    for line in md.split("\n"):
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return fallback


def build_sidebar(nav, current, site_title):
    links = []
    for fname, title in nav:
        cls = ' class="active"' if fname == current else ""
        links.append(f'<li{cls}><a href="{html.escape(fname)}">{html.escape(title)}</a></li>')
    return (f'<nav class="sidebar"><div class="site-title">{html.escape(site_title)}</div>'
            f'<ul>{"".join(links)}</ul></nav>')


def build_toc(toc):
    # ページ内目次(h2/h3 のみ)
    items = [(lvl, txt, hid) for lvl, txt, hid in toc if lvl in (2, 3)]
    if len(items) < 2:
        return ""
    lis = []
    for lvl, txt, hid in items:
        cls = "toc-2" if lvl == 2 else "toc-3"
        lis.append(f'<li class="{cls}"><a href="#{hid}">{html.escape(txt)}</a></li>')
    return f'<div class="page-toc"><p class="toc-head">目次</p><ul>{"".join(lis)}</ul></div>'


def render_page(title, site_title, sidebar, toc_html, body, mermaid_ref):
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
{sidebar}
<main class="content">
{toc_html}
<article class="markdown-body">
{body}
</article>
</main>
</div>
{mermaid_ref}
<script>
  if (window.mermaid) {{
    mermaid.initialize({{ startOnLoad: true, theme: "default", securityLevel: "loose" }});
  }}
</script>
</body>
</html>
"""


CSS = """
:root{--fg:#1f2328;--muted:#59636e;--bd:#d1d9e0;--bg:#fff;--sidebar:#f6f8fa;
--link:#0969da;--code-bg:#f6f8fa;--th-bg:#f6f8fa;}
*{box-sizing:border-box;}
body{margin:0;color:var(--fg);background:var(--bg);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;
line-height:1.7;font-size:16px;}
.layout{display:flex;min-height:100vh;}
.sidebar{width:260px;flex:0 0 260px;background:var(--sidebar);border-right:1px solid var(--bd);
padding:20px 16px;position:sticky;top:0;height:100vh;overflow-y:auto;}
.sidebar .site-title{font-weight:700;font-size:15px;margin-bottom:14px;color:var(--fg);}
.sidebar ul{list-style:none;margin:0;padding:0;}
.sidebar li{margin:2px 0;}
.sidebar a{display:block;padding:6px 10px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:14px;}
.sidebar a:hover{background:#eaeef2;}
.sidebar li.active a{background:var(--link);color:#fff;font-weight:600;}
.content{flex:1;min-width:0;padding:32px 48px;max-width:1000px;}
.page-toc{float:right;width:220px;margin:0 0 20px 24px;padding:12px 16px;background:var(--sidebar);
border:1px solid var(--bd);border-radius:8px;font-size:13px;}
.page-toc .toc-head{font-weight:700;margin:0 0 8px;}
.page-toc ul{list-style:none;margin:0;padding:0;}
.page-toc li.toc-3{padding-left:14px;}
.page-toc a{color:var(--muted);text-decoration:none;}
.page-toc a:hover{color:var(--link);}
.markdown-body h1{font-size:28px;border-bottom:2px solid var(--bd);padding-bottom:10px;margin-top:0;}
.markdown-body h2{font-size:22px;border-bottom:1px solid var(--bd);padding-bottom:6px;margin-top:32px;}
.markdown-body h3{font-size:18px;margin-top:26px;}
.markdown-body h4{font-size:16px;margin-top:22px;}
a{color:var(--link);}
.table-wrap{overflow-x:auto;margin:16px 0;}
table{border-collapse:collapse;width:100%;font-size:14px;}
th,td{border:1px solid var(--bd);padding:7px 12px;text-align:left;vertical-align:top;}
th{background:var(--th-bg);font-weight:600;}
tr:nth-child(even) td{background:#fafbfc;}
code{background:var(--code-bg);padding:2px 6px;border-radius:5px;font-size:85%;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.codeblock{position:relative;margin:16px 0;}
.codeblock .code-lang{position:absolute;top:0;right:0;background:#e7ecf1;color:var(--muted);
font-size:11px;padding:2px 8px;border-radius:0 6px 0 6px;text-transform:uppercase;letter-spacing:.05em;}
.codeblock pre{background:var(--code-bg);border:1px solid var(--bd);border-radius:8px;
padding:14px 16px;overflow-x:auto;margin:0;}
.codeblock pre code{background:none;padding:0;font-size:13px;line-height:1.5;}
pre.mermaid{background:#fff;text-align:center;margin:20px 0;}
blockquote{border-left:4px solid var(--bd);margin:16px 0;padding:2px 16px;color:var(--muted);}
.admonition{border:1px solid var(--bd);border-left-width:4px;border-radius:8px;
padding:12px 16px;margin:16px 0;background:#fff;}
.admonition-title{font-weight:700;margin:0 0 6px;font-size:14px;}
.admonition p:last-child{margin-bottom:0;}
.admonition-note{border-left-color:#0969da;background:#ddf4ff40;}
.admonition-note .admonition-title{color:#0969da;}
.admonition-tip{border-left-color:#1a7f37;background:#dafbe140;}
.admonition-tip .admonition-title{color:#1a7f37;}
.admonition-important{border-left-color:#8250df;background:#fbefff40;}
.admonition-important .admonition-title{color:#8250df;}
.admonition-warning{border-left-color:#9a6700;background:#fff8c540;}
.admonition-warning .admonition-title{color:#9a6700;}
.admonition-caution{border-left-color:#cf222e;background:#ffebe940;}
.admonition-caution .admonition-title{color:#cf222e;}
ul,ol{padding-left:24px;}
li.task{list-style:none;margin-left:-20px;}
li.task .checkbox{color:var(--muted);}
li.task.done{color:var(--muted);}
hr{border:none;border-top:1px solid var(--bd);margin:28px 0;}
@media(max-width:820px){
.layout{flex-direction:column;}
.sidebar{width:100%;height:auto;position:static;border-right:none;border-bottom:1px solid var(--bd);}
.content{padding:20px;}
.page-toc{float:none;width:auto;margin:0 0 16px;}
}
"""


def main():
    ap = argparse.ArgumentParser(description="Markdown 仕様書を完全オフライン HTML に変換")
    ap.add_argument("input", help="入力ディレクトリ、または単一の .md ファイル")
    ap.add_argument("output", help="出力ディレクトリ")
    ap.add_argument("--title", default=None, help="サイドバー見出しに使うタイトル")
    ap.add_argument("--standalone", action="store_true",
                    help="mermaid を各 HTML に埋め込み 1 ファイルで完結させる")
    args = ap.parse_args()

    # 入力ファイルの収集
    if os.path.isdir(args.input):
        md_files = sorted(
            [f for f in os.listdir(args.input) if f.endswith(".md")],
            key=lambda f: (f != "index.md", f),  # index.md を先頭に
        )
        in_dir = args.input
    else:
        in_dir = os.path.dirname(args.input) or "."
        md_files = [os.path.basename(args.input)]

    if not md_files:
        print(f"[エラー] {args.input} に .md ファイルが見つかりません", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    # ナビ用に (html名, タイトル) を先に集める
    nav = []
    contents = {}
    for f in md_files:
        with open(os.path.join(in_dir, f), encoding="utf-8") as fp:
            md = fp.read()
        contents[f] = md
        title = page_title(md, os.path.splitext(f)[0])
        nav.append((os.path.splitext(f)[0] + ".html", title))

    site_title = args.title or (nav[0][1] if nav else "仕様書")

    # mermaid の配置
    mermaid_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "mermaid.min.js")
    mermaid_src = os.path.normpath(mermaid_src)
    if args.standalone:
        with open(mermaid_src, encoding="utf-8") as fp:
            mermaid_js = fp.read()
        mermaid_ref = f"<script>{mermaid_js}</script>"
    else:
        assets_out = os.path.join(args.output, "assets")
        os.makedirs(assets_out, exist_ok=True)
        shutil.copy(mermaid_src, os.path.join(assets_out, "mermaid.min.js"))
        mermaid_ref = '<script src="assets/mermaid.min.js"></script>'

    # 各ページを変換
    for f in md_files:
        html_name = os.path.splitext(f)[0] + ".html"
        toc = []
        body = convert(contents[f], toc)
        title = page_title(contents[f], os.path.splitext(f)[0])
        sidebar = build_sidebar(nav, html_name, site_title)
        toc_html = build_toc(toc)
        page = render_page(title, site_title, sidebar, toc_html, body, mermaid_ref)
        with open(os.path.join(args.output, html_name), "w", encoding="utf-8") as fp:
            fp.write(page)
        print(f"  ✓ {f} → {html_name}")

    print(f"\n完了: {len(md_files)} ファイルを {args.output} に出力しました。")
    print(f"ブラウザで開く: {os.path.join(args.output, nav[0][0])}")


if __name__ == "__main__":
    main()
