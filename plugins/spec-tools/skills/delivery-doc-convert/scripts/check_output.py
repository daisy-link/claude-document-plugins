#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成したHTMLを点検する（リンク切れ・変換漏れ）。

使い方:
    python3 check_output.py HTML
"""
from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "HTML").resolve()
    if not out.is_dir():
        print(f"出力先が見つかりません: {out}")
        return 1

    pages = sorted(out.rglob("*.html"))
    broken: list[str] = []
    leftovers: list[str] = []

    for page in pages:
        body = page.read_text(encoding="utf-8")
        name = page.relative_to(out).as_posix()

        for href in re.findall(r'(?:href|src)="([^"]+)"', body):
            if href.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = (page.parent / urllib.parse.unquote(href.split("#")[0])).resolve()
            if not target.exists():
                broken.append(f"{name} → {href}")

        if "xxblockxx" in body:
            leftovers.append(f"{name}: 図・注記の差し込みが残っています")
        if re.search(r"\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", body):
            leftovers.append(f"{name}: 注記記法が変換されていません")
        if re.search(r'href="[^"]+\.md"', body):
            leftovers.append(f"{name}: .md のままのリンクが残っています")

    print(f"ページ数: {len(pages)}")
    print(f"リンク切れ: {len(broken)}")
    for item in broken[:20]:
        print("   ", item)
    print(f"変換漏れ: {len(leftovers)}")
    for item in leftovers[:20]:
        print("   ", item)
    return 1 if broken or leftovers else 0


if __name__ == "__main__":
    raise SystemExit(main())
