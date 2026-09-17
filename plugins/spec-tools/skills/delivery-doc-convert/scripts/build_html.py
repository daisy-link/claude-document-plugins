#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown の設計書一式を、サイドバー付きの静的HTMLに変換する。

使い方（プロジェクトのルートで実行）:
    python3 <skill>/scripts/build_html.py
    python3 <skill>/scripts/build_html.py --src docs --out HTML --title "◯◯システム 仕様書"

出力先ディレクトリは毎回作り直すため、生成物以外を置かないこと。
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import unicodedata
import urllib.parse
from pathlib import Path

try:
    import markdown
except ImportError:  # pragma: no cover
    raise SystemExit("python-markdown が必要です:  pip3 install markdown")

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STYLE = SKILL_DIR / "assets" / "style.css"

# GitHub 形式の注記（> [!NOTE] など）の対応表
ALERT_LABEL = {
    "NOTE": ("補足", "note"),
    "TIP": ("ヒント", "tip"),
    "IMPORTANT": ("重要", "important"),
    "WARNING": ("注意", "warning"),
    "CAUTION": ("ご注意ください", "caution"),
}

# 表の列を折り返さない判定に使う幅（半角換算。全角文字は2）
NOWRAP_WIDTH = 24          # 列全体がこの幅に収まるなら、その列は折り返さない
LABEL_NOWRAP_WIDTH = 40    # 「項目」系の列で、1マスごとに折り返さない上限
LABEL_HEADERS = {
    "項目", "項目名", "設定項目", "名称", "画面名", "機能", "機能名", "操作名",
    "区分", "種類", "値", "設定", "状態", "対象", "表示名", "種別",
}

MD_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "attr_list", "toc"]

MERMAID = """<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: false, theme: "neutral", fontFamily: "inherit" });
  // 図ごとにIDを決めて1枚ずつ描く。まとめて描かせるとIDが衝突して、
  // 別の図の中身が1枚に混ざったり、空の図ができたりすることがある。
  const nodes = [...document.querySelectorAll("pre.mermaid")];
  for (let i = 0; i < nodes.length; i++) {
    const { svg } = await mermaid.render("diagram" + i, nodes[i].textContent);
    nodes[i].innerHTML = svg;
    nodes[i].setAttribute("data-processed", "true");
  }
</script>
"""

PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}{title_sep}{site}</title>
<link rel="stylesheet" href="{assets}style.css">
</head>
<body>
<input type="checkbox" id="nav-toggle" class="nav-toggle">
<header class="site-header">
  <label for="nav-toggle" class="nav-button" aria-label="資料一覧を開く">☰</label>
  <a class="site-title" href="{home}">
    <span class="site-title-main">{site}</span>
    {subtitle_html}
  </a>
</header>
<div class="layout">
  <div class="sidebar">
    {nav}
  </div>
  <main class="content">
    <nav class="breadcrumb" aria-label="現在位置">{breadcrumb}</nav>
    <article class="doc">
{body}
    </article>
    <nav class="pager">{pager}</nav>
    <footer class="site-footer">{footer}</footer>
  </main>
</div>
{mermaid}
</body>
</html>
"""


# --------------------------------------------------------------------------
# 共通のちいさな道具
# --------------------------------------------------------------------------
def slugify(value: str, separator: str = "-") -> str:
    """日本語の見出しでも id が空にならないスラグを作る。"""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"\s+", separator, value)
    value = re.sub(r"[^\w\-ぁ-んァ-ヶ一-龥ー]", "", value)
    return value or "section"


def read_title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def text_width(value: str) -> int:
    """タグを除いた表示幅（全角2・半角1）を返す。"""
    text = html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in text)


def rel_href(from_rel: str, to_rel: str) -> str:
    depth = len(Path(from_rel).parts) - 1
    return "../" * depth + to_rel.replace(".md", ".html")


# --------------------------------------------------------------------------
# ページの並び順（資料の index.md のリンク順を尊重する）
# --------------------------------------------------------------------------
def local_md_links(path: Path, src: Path) -> list[str]:
    """Markdown 内のローカル .md リンクを、書かれている順に返す。"""
    found: list[str] = []
    for href in re.findall(r"\]\(([^)]+\.md)\)", path.read_text(encoding="utf-8")):
        target = (path.parent / urllib.parse.unquote(href)).resolve()
        try:
            rel = target.relative_to(src.resolve()).as_posix()
        except ValueError:
            continue
        if target.exists() and rel not in found:
            found.append(rel)
    return found


def collect_pages(src: Path) -> list[dict]:
    """[{"rel": ..., "children": [...]}] をナビの並び順で返す。

    並び順は index.md のリンク順を基本にし、リンクされていないファイルは
    ファイル名順で後ろに足す（新しい資料を作っても取りこぼさないため）。
    """
    entries: list[dict] = []
    sections: dict[str, dict] = {}
    placed: set[str] = set()

    def add_section(folder: str) -> dict:
        if folder in sections:
            return sections[folder]
        index_rel = f"{folder}/index.md"
        section = {
            "rel": index_rel if (src / index_rel).exists() else None,
            "folder": folder,
            "children": [],
        }
        sections[folder] = section
        entries.append(section)
        if section["rel"]:
            placed.add(index_rel)
            for child in local_md_links(src / index_rel, src):
                if child.startswith(f"{folder}/") and child not in placed:
                    section["children"].append(child)
                    placed.add(child)
        return section

    def add(rel: str) -> None:
        if rel in placed:
            return
        folder = Path(rel).parent.as_posix()
        if folder == ".":
            entries.append({"rel": rel, "children": []})
            placed.add(rel)
            return
        section = add_section(folder)
        if rel not in placed:
            section["children"].append(rel)
            placed.add(rel)

    root_index = src / "index.md"
    if root_index.exists():
        add("index.md")
        for rel in local_md_links(root_index, src):
            add(rel)

    # index.md から辿れなかった資料を拾う
    for path in sorted(src.rglob("*.md"), key=lambda p: p.relative_to(src).as_posix()):
        add(path.relative_to(src).as_posix())

    return [e for e in entries if e["rel"] or e["children"]]


# --------------------------------------------------------------------------
# Markdown → HTML
# --------------------------------------------------------------------------
def md_convert(text: str) -> str:
    converter = markdown.Markdown(
        extensions=MD_EXTENSIONS, extension_configs={"toc": {"slugify": slugify}}
    )
    return converter.convert(text)


def extract_blocks(text: str) -> tuple[str, list[str]]:
    """mermaid と注記引用を、変換の邪魔にならないよう一旦退避する。"""
    blocks: list[str] = []

    def store(fragment: str) -> str:
        blocks.append(fragment)
        return f"\n\nxxblockxx{len(blocks) - 1}xx\n\n"

    text = re.sub(
        r"```mermaid\n(.*?)\n```",
        lambda m: store(f'<div class="mermaid-wrap"><pre class="mermaid">{html.escape(m.group(1))}</pre></div>'),
        text,
        flags=re.S,
    )

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        kind = m.group(1)
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].startswith(">"):
            body.append(re.sub(r"^>\s?", "", lines[i]))
            i += 1
        label, css = ALERT_LABEL[kind]
        inner = md_convert("\n".join(body))
        out.append(store(f'<div class="alert alert-{css}"><p class="alert-title">{label}</p>{inner}</div>').strip())
    return "\n".join(out), blocks


CELL_RE = re.compile(r"<(th|td)([^>]*)>(.*?)</\1>", re.S)
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
TABLE_RE = re.compile(r"<table>(.*?)</table>", re.S)


def apply_nowrap(body: str) -> str:
    """No・項目名など、短い内容の列が途中で折り返さないようにする。

    表は読み合わせに使われるため、名称が2行に割れると視線が飛んでしまう。
    一方で全部を折り返し禁止にすると横に伸びてしまうので、
    「列全体が短い」か「項目名の列で、そのマスが長すぎない」場合だけ適用する。
    """

    def fix_table(m: re.Match) -> str:
        inner = m.group(1)
        rows = [CELL_RE.findall(row) for row in ROW_RE.findall(inner)]
        if not rows:
            return m.group(0)

        width: dict[int, int] = {}
        for row in rows:
            for col, (_tag, _attrs, content) in enumerate(row):
                width[col] = max(width.get(col, 0), text_width(content))
        nowrap_cols = {col for col, w in width.items() if w <= NOWRAP_WIDTH}
        label_cols = {
            col for col, (_t, _a, content) in enumerate(rows[0])
            if html.unescape(re.sub(r"<[^>]+>", "", content)).strip() in LABEL_HEADERS
        } - nowrap_cols
        if not nowrap_cols and not label_cols:
            return m.group(0)

        def fix_row(rm: re.Match) -> str:
            col = {"n": 0}

            def fix_cell(cm: re.Match) -> str:
                tag, attrs, content = cm.groups()
                index = col["n"]
                col["n"] += 1
                if index in nowrap_cols or (
                    index in label_cols and text_width(content) <= LABEL_NOWRAP_WIDTH
                ):
                    return f'<{tag}{attrs} class="nowrap">{content}</{tag}>'
                return cm.group(0)

            return "<tr>" + CELL_RE.sub(fix_cell, rm.group(1)) + "</tr>"

        return "<table>" + ROW_RE.sub(fix_row, inner) + "</table>"

    return TABLE_RE.sub(fix_table, body)


def rewrite_links(body: str) -> str:
    """資料どうしのリンクを .md から .html に差し替える（表示文字列も含む）。"""

    def sub(m: re.Match) -> str:
        href = m.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        return 'href="%s"' % re.sub(r"\.md(#|$)", r".html\1", href)

    body = re.sub(r'href="([^"]+)"', sub, body)
    return re.sub(r'([^<>"\s]+)\.md(</a>)', r"\1.html\2", body)


def build_body(text: str) -> str:
    text, blocks = extract_blocks(text)
    body = md_convert(text)
    for idx, fragment in enumerate(blocks):
        body = body.replace(f"<p>xxblockxx{idx}xx</p>", fragment).replace(f"xxblockxx{idx}xx", fragment)
    body = apply_nowrap(body)
    # 横に長い表はその表だけスクロールできるようにする
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    return rewrite_links(body)


# --------------------------------------------------------------------------
# ページ組み立て
# --------------------------------------------------------------------------
def render_nav(entries: list[dict], current: str, titles: dict[str, str], home_label: str) -> str:
    def link(rel: str, label: str | None = None, cls: str = "") -> str:
        classes = " ".join(filter(None, [cls, "current" if rel == current else ""]))
        attr = f' class="{classes}"' if classes else ""
        return f'<a{attr} href="{rel_href(current, rel)}">{html.escape(label or titles[rel])}</a>'

    parts = ['<nav class="side-nav" aria-label="資料一覧">', "<ul>"]
    for entry in entries:
        rel = entry["rel"]
        if not entry["children"]:
            label = home_label if rel == "index.md" else None
            parts.append(f"<li>{link(rel, label)}</li>")
            continue
        parts.append("</ul>")
        heading = link(rel, cls="nav-group-link") if rel else html.escape(entry["folder"])
        parts.append(f'<p class="nav-group">{heading}</p>')
        parts.append("<ul>")
        parts.extend(f"<li>{link(child)}</li>" for child in entry["children"])
        parts.append("</ul>")
        parts.append("<ul>")
    parts.append("</ul></nav>")
    return re.sub(r"<ul>\s*</ul>", "", "\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="設計書MarkdownをHTML版に変換します")
    parser.add_argument("--src", default="docs", help="変換元のフォルダ（既定: docs）")
    parser.add_argument("--out", default="HTML", help="出力先のフォルダ（既定: HTML）")
    parser.add_argument("--title", default=None, help="ヘッダーに出すシステム名（既定: index.md の見出し）")
    parser.add_argument("--subtitle", default="", help="ヘッダーの補足（例: 仕様書）")
    parser.add_argument("--home-label", default="ドキュメント一覧", help="トップページの呼び名")
    parser.add_argument("--style", default=str(DEFAULT_STYLE), help="使用するCSSファイル")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    if not src.is_dir():
        raise SystemExit(f"変換元が見つかりません: {src}")

    entries = collect_pages(src)
    rels = [rel for e in entries for rel in ([e["rel"]] if e["rel"] else []) + e["children"]]
    if not rels:
        raise SystemExit(f"Markdown が見つかりません: {src}")
    titles = {rel: read_title(src / rel) for rel in rels}

    site = args.title or (titles.get("index.md") or src.name)
    subtitle_html = f'<span class="site-title-sub">{html.escape(args.subtitle)}</span>' if args.subtitle else ""
    footer = " ".join(filter(None, [site, args.subtitle]))

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    shutil.copyfile(args.style, out / "assets" / "style.css")

    for pos, rel in enumerate(rels):
        text = (src / rel).read_text(encoding="utf-8")
        # 「← 一覧に戻る」はパンくずと重複するので落とし、直後の区切り線も詰める
        text = re.sub(r"^\[← [^\]]+\]\([^)]+\)\s*$", "", text, count=1, flags=re.M)
        text = re.sub(r"\A(# .*\n)\s*(?:---[ \t]*\n)+", r"\1\n", text)
        body = build_body(text)

        depth = len(Path(rel).parts) - 1
        home = rel_href(rel, "index.md") if "index.md" in titles else "#"

        crumbs = []
        if "index.md" in titles:
            crumbs.append(f'<a href="{home}">{html.escape(args.home_label)}</a>')
        parent = Path(rel).parent.as_posix()
        if parent != "." and not rel.endswith("/index.md"):
            parent_rel = f"{parent}/index.md"
            if parent_rel in titles:
                crumbs.append(f'<a href="{rel_href(rel, parent_rel)}">{html.escape(titles[parent_rel])}</a>')
        if rel != "index.md":
            crumbs.append(f"<span>{html.escape(titles[rel])}</span>")

        pager = []
        if pos > 0:
            prev = rels[pos - 1]
            pager.append(f'<a class="pager-prev" href="{rel_href(rel, prev)}"><span>前の資料</span>{html.escape(titles[prev])}</a>')
        if pos < len(rels) - 1:
            nxt = rels[pos + 1]
            pager.append(f'<a class="pager-next" href="{rel_href(rel, nxt)}"><span>次の資料</span>{html.escape(titles[nxt])}</a>')

        page = PAGE.format(
            title=html.escape(titles[rel]),
            title_sep=" | " if titles[rel] != site else "",
            site=html.escape(site),
            subtitle_html=subtitle_html,
            assets="../" * depth + "assets/",
            home=home,
            nav=render_nav(entries, rel, titles, args.home_label),
            breadcrumb='<span class="sep">/</span>'.join(crumbs),
            body=body,
            pager="".join(pager),
            footer=html.escape(footer),
            mermaid=MERMAID if 'class="mermaid"' in body else "",
        )
        dest = out / rel.replace(".md", ".html")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8")

    print(f"{len(rels)} ページを生成しました: {out}")


if __name__ == "__main__":
    main()
