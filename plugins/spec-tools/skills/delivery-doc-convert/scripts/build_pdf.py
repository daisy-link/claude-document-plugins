#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown の設計書一式を、表紙・目次・ページ番号付きの1冊のPDFにまとめる。

使い方（プロジェクトのルートで実行）:
    python3 <skill>/scripts/build_pdf.py
    python3 <skill>/scripts/build_pdf.py --src docs --out PDF \
        --title "○○株式会社" --subtitle "△△システム 仕様書"

HTML版（build_html.py）と同じ変換処理を使うため、両者の内容は必ず一致する。
"""
from __future__ import annotations

import argparse
import datetime
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_html import (  # noqa: E402
    SKILL_DIR,
    build_body,
    collect_pages,
    read_title,
    slugify,
)

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover
    raise SystemExit("pypdf が必要です:  pip3 install pypdf")

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "chromium",
]

MERMAID = """<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "neutral", fontFamily: "inherit" });
</script>
"""


def find_chrome(explicit: str | None) -> str:
    for candidate in filter(None, [explicit, *CHROME_CANDIDATES]):
        path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if path:
            return path
    raise SystemExit("Google Chrome が見つかりません。--chrome でパスを指定してください")


def print_pdf(chrome: str, source: Path, dest: Path, wait_ms: int) -> None:
    """Chrome のヘッドレスモードでHTMLをPDFに印刷する。"""
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        f"--virtual-time-budget={wait_ms}",
        f"--print-to-pdf={dest}",
        source.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not dest.exists():
        raise SystemExit(f"PDFの生成に失敗しました:\n{result.stderr[-2000:]}")


# --------------------------------------------------------------------------
# 本文の組み立て
# --------------------------------------------------------------------------
def anchor_id(rel: str) -> str:
    return "doc-" + slugify(rel[: -len(".md")].replace("/", "-"))


def localize(body: str, rel: str, pages: list[str], section_id: str) -> str:
    """資料間リンクをPDF内リンクに、見出しidを資料ごとに一意にする。"""

    def fix_href(m: re.Match) -> str:
        href = m.group(1)
        if href.startswith(("http://", "https://", "mailto:")):
            return m.group(0)
        if href.startswith("#"):
            return f'href="#{section_id}-{href[1:]}"'
        target = (Path(rel).parent / href.replace(".html", ".md")).as_posix()
        target = Path(target).as_posix().replace("../", "")
        if target in pages:
            return f'href="#{anchor_id(target)}"'
        return m.group(0)

    body = re.sub(r'href="([^"]+)"', fix_href, body)
    # PDFではファイル名の拡張子は意味がないので、表示上は落とす
    body = re.sub(r'([^<>"\s]+)\.html(</a>)', r"\1\2", body)
    return re.sub(r'\bid="([^"]+)"', lambda m: f'id="{section_id}-{m.group(1)}"', body)


MERMAID_RE = re.compile(r'<pre class="mermaid">(.*?)</pre>', re.S)


def build_sections(src: Path, pages: list[str]) -> list[str]:
    """資料ごとの本文HTMLを、PDF用に作る。"""
    sections = []
    for index, rel in enumerate(pages):
        text = (src / rel).read_text(encoding="utf-8")
        text = re.sub(r"^\[← [^\]]+\]\([^)]+\)\s*$", "", text, count=1, flags=re.M)
        text = re.sub(r"\A(# .*\n)\s*(?:---[ \t]*\n)+", r"\1\n", text)
        body = localize(build_body(text), rel, pages, anchor_id(rel))
        sections.append(
            f'<article class="doc" id="{anchor_id(rel)}">'
            f'<span class="pdf-marker">@@S{index}@@</span>\n{body}\n</article>'
        )
    return sections


def prerender_mermaid(chrome: str, sections: list[str], wait_ms: int) -> tuple[list[str], bool]:
    """図を先にSVGへ描き起こし、本文に埋め込んで返す。

    印刷時にブラウザ側で図を描かせると、描画の途中で紙面が確定してしまい、
    図が空になったり別の図と重なったりする。先に描いてからSVGを貼り込めば、
    印刷は静止したHTMLに対して行われるので結果が安定する。

    取り出し方にも注意がいる。描画ライブラリは寸法を測るための一時的なSVGも
    ページに残すため、DOM全体からSVGを拾うと図と一時物が混ざってしまう。
    そこで描画後にページ自身に「図だけを目印付きで並べ直させて」から取り出す。
    """
    codes = [m.group(1) for section in sections for m in MERMAID_RE.finditer(section)]
    if not codes:
        return sections, True

    collector = """<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: false, theme: "neutral", fontFamily: "inherit" });
  // 図ごとに自分でIDを決めて1枚ずつ描く。まとめて描かせるとIDが衝突し、
  // 別の図の中身が1枚に混ざったり、空の図ができたりする。
  const nodes = [...document.querySelectorAll("pre.mermaid")];
  const drawn = [];
  for (let i = 0; i < nodes.length; i++) {
    const { svg } = await mermaid.render("pdfdiagram" + i, nodes[i].textContent);
    drawn.push(svg);
  }
  document.body.innerHTML = drawn.map((svg, i) => `<!--@@D${i}@@-->` + svg).join("");
</script>"""

    page = (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><style>'
        'body { width: 1000px; font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans",'
        ' "Noto Sans JP", sans-serif; }</style></head><body>'
        + "".join(f'<pre class="mermaid">{code}</pre>' for code in codes)
        + collector
        + "</body></html>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "mermaid.html"
        source.write_text(page, encoding="utf-8")
        result = subprocess.run(
            [chrome, "--headless", "--disable-gpu", f"--virtual-time-budget={wait_ms}",
             "--dump-dom", source.as_uri()],
            capture_output=True, text=True,
        )

    svgs: list[str] = []
    for index in range(len(codes)):
        chunk = re.search(
            rf"<!--@@D{index}@@-->(.*?)(?=<!--@@D{index + 1}@@-->|</body>)",
            result.stdout, re.S,
        )
        found = re.search(r"<svg\b.*</svg>", chunk.group(1), re.S) if chunk else None
        if not found:
            return sections, False
        svg = found.group(0)
        # 図ごとに id が重なると互いの配色や配置が壊れるので、必ず一意にする
        original = re.search(r'id="([^"]+)"', svg)
        if original:
            svg = svg.replace(original.group(1), f"pdf-diagram-{index}")
        svgs.append(svg)

    drawn = iter(svgs)
    return [MERMAID_RE.sub(lambda m: next(drawn), section) for section in sections], True


def chapter_numbers(entries: list[dict]) -> dict[str, str]:
    """資料に 1 / 2.1 のような章番号を振る。"""
    numbers: dict[str, str] = {}
    top = 0
    for entry in entries:
        top += 1
        if entry["rel"]:
            numbers[entry["rel"]] = str(top)
        for i, child in enumerate(entry["children"], start=1):
            numbers[child] = f"{top}.{i}"
    return numbers


def render_toc(entries: list[dict], titles: dict[str, str], numbers: dict[str, str],
               start_pages: dict[str, int] | None, home_label: str) -> str:
    rows = ["<section class=\"toc\"><h2>目次</h2><ol>"]
    for entry in entries:
        for rel, child in [(entry["rel"], False)] + [(c, True) for c in entry["children"]]:
            if not rel:
                continue
            page = start_pages.get(rel) if start_pages else None
            label = home_label if rel == "index.md" else titles[rel]
            rows.append(
                f'<li class="{"toc-child" if child else "toc-parent"}">'
                f'<a class="toc-row" href="#{anchor_id(rel)}">'
                f'<span class="toc-no">{numbers.get(rel, "")}</span>'
                f'<span class="toc-name">{html.escape(label)}</span>'
                f'<span class="toc-dots"></span>'
                f'<span class="toc-page">{page if page else ""}</span>'
                f"</a></li>"
            )
    rows.append("</ol></section>")
    return "\n".join(rows)


def build_print_html(entries: list[dict], titles: dict[str, str], sections: list[str],
                     site: str, subtitle: str, date: str, css: str,
                     start_pages: dict[str, int] | None, home_label: str,
                     draw_in_browser: bool) -> str:
    numbers = chapter_numbers(entries)

    cover = (
        '<section class="cover">'
        '<div class="cover-rule"></div>'
        f'<h1 class="cover-title">{html.escape(site)}</h1>'
        + (f'<p class="cover-subtitle">{html.escape(subtitle)}</p>' if subtitle else "")
        + f'<p class="cover-meta">{html.escape(date)}</p>'
        "</section>"
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{html.escape(" ".join(filter(None, [site, subtitle])))}</title>
<style>
{css}
</style>
</head>
<body>
{cover}
{render_toc(entries, titles, numbers, start_pages, home_label)}
{"".join(sections)}
{MERMAID if draw_in_browser else ""}
</body>
</html>
"""


def build_footer_html(page_count: int, labels: dict[int, str], skip: set[int]) -> str:
    """ページ番号と資料名を重ねるための透明なPDFを作るHTML。"""
    blocks = []
    for page in range(1, page_count + 1):
        if page in skip:
            blocks.append('<div class="sheet"></div>')
            continue
        label = html.escape(labels.get(page, ""))
        blocks.append(
            f'<div class="sheet"><div class="foot">'
            f'<span class="foot-left">{label}</span>'
            f'<span class="foot-right">{page} / {page_count}</span>'
            "</div></div>"
        )
    return """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><style>
@page { size: A4; margin: 0; }
body { margin: 0; font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans", sans-serif; }
.sheet { position: relative; width: 210mm; height: 297mm; page-break-after: always; }
.sheet:last-child { page-break-after: auto; }
.foot {
  position: absolute; left: 16mm; right: 16mm; bottom: 10mm;
  display: flex; justify-content: space-between;
  font-size: 8pt; color: #7b858e;
}
</style></head><body>
""" + "\n".join(blocks) + "</body></html>"


def find_start_pages(pdf: Path, pages: list[str]) -> dict[str, int]:
    """PDF本文から目印を探して、各資料の開始ページを求める。"""
    reader = PdfReader(str(pdf))
    found: dict[str, int] = {}
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for match in re.finditer(r"@@S(\d+)@@", text.replace("\n", "")):
            rel = pages[int(match.group(1))]
            found.setdefault(rel, number)
    return found


def stamp_footer(chrome: str, content: Path, out: Path, labels: dict[int, str], skip: set[int]) -> int:
    """全ページにページ番号と資料名を重ねる。

    重ね合わせるとページの中身が展開されてファイルが数倍に膨らむので、
    最後に圧縮し直している（116ページで 23MB → 5MB 程度）。
    """
    writer = PdfWriter(clone_from=str(content))
    count = len(writer.pages)
    with tempfile.TemporaryDirectory() as tmp:
        footer_html = Path(tmp) / "footer.html"
        footer_pdf = Path(tmp) / "footer.pdf"
        footer_html.write_text(build_footer_html(count, labels, skip), encoding="utf-8")
        print_pdf(chrome, footer_html, footer_pdf, 5000)

        overlay = PdfReader(str(footer_pdf))
        for index, page in enumerate(writer.pages):
            if index < len(overlay.pages):
                page.merge_page(overlay.pages[index])
            page.compress_content_streams()
    writer.compress_identical_objects()
    with out.open("wb") as fh:
        writer.write(fh)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="設計書Markdownを1冊のPDFにまとめます")
    parser.add_argument("--src", default="docs", help="変換元のフォルダ（既定: docs）")
    parser.add_argument("--out", default="PDF", help="出力先のフォルダ（既定: PDF）")
    parser.add_argument("--name", default=None, help="PDFのファイル名（既定: タイトルから作成）")
    parser.add_argument("--title", default=None, help="表紙の主題（既定: index.md の見出し）")
    parser.add_argument("--subtitle", default="", help="表紙の副題（例: 仕様書）")
    parser.add_argument("--date", default=None, help="表紙に出す日付（既定: 本日）")
    parser.add_argument("--home-label", default="ドキュメント一覧", help="トップページの呼び名")
    parser.add_argument("--style", default=str(SKILL_DIR / "assets" / "print.css"), help="印刷用CSS")
    parser.add_argument("--chrome", default=None, help="Chrome の実行ファイル")
    parser.add_argument("--wait", type=int, default=20000, help="図の描画を待つ時間(ms)")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        raise SystemExit(f"変換元が見つかりません: {src}")

    chrome = find_chrome(args.chrome)
    entries = collect_pages(src)
    pages = [rel for e in entries for rel in ([e["rel"]] if e["rel"] else []) + e["children"]]
    if not pages:
        raise SystemExit(f"Markdown が見つかりません: {src}")
    titles = {rel: read_title(src / rel) for rel in pages}

    site = args.title or titles.get("index.md") or src.name
    date = args.date or datetime.date.today().strftime("%Y年%m月%d日")
    css = Path(args.style).read_text(encoding="utf-8")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or (re.sub(r"[\\/:*?\"<>|\s]+", "_", " ".join(filter(None, [site, args.subtitle]))) + ".pdf")
    final = out_dir / name

    sections = build_sections(src, pages)
    sections, drawn = prerender_mermaid(chrome, sections, args.wait)
    if not drawn:
        print("図の事前描画に失敗したため、印刷時に描画します（図が乱れる場合は再実行してください）")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        draft_html = tmp_dir / "draft.html"
        draft_pdf = tmp_dir / "draft.pdf"
        final_html = tmp_dir / "print.html"
        content_pdf = tmp_dir / "content.pdf"

        # 1回目: 目次のページ番号を調べるための下刷り
        draft_html.write_text(
            build_print_html(entries, titles, sections, site, args.subtitle, date, css, None,
                             args.home_label, not drawn),
            encoding="utf-8",
        )
        print_pdf(chrome, draft_html, draft_pdf, args.wait)
        start_pages = find_start_pages(draft_pdf, pages)

        # 2回目: 目次にページ番号を入れて本刷り
        final_html.write_text(
            build_print_html(entries, titles, sections, site, args.subtitle, date, css, start_pages,
                             args.home_label, not drawn),
            encoding="utf-8",
        )
        print_pdf(chrome, final_html, content_pdf, args.wait)

        # 各ページがどの資料かを求め、ページ番号と資料名を重ねる
        starts = find_start_pages(content_pdf, pages)
        total = len(PdfReader(str(content_pdf)).pages)
        labels: dict[int, str] = {}
        current = ""
        by_page = {page: titles[rel] for rel, page in starts.items()}
        for page in range(1, total + 1):
            current = by_page.get(page, current)
            labels[page] = current
        count = stamp_footer(chrome, content_pdf, final, labels, skip={1})

    missing = [titles[rel] for rel in pages if rel not in start_pages]
    print(f"{count} ページのPDFを生成しました: {final}")
    print(f"収録資料: {len(pages)} 件")
    if missing:
        print("目次のページ番号を特定できなかった資料:", "、".join(missing))


if __name__ == "__main__":
    main()
