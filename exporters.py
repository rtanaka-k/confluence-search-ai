"""
exporters.py
検索結果のローカル出力（CSV / PDF）。

Confluence への書き込みは行わない（認証基盤の話のためスコープ外）。
ここで生成するのは、利用者の手元に落とすファイルのみ。
"""

import io
import csv
from datetime import datetime

from search_areas import SEARCH_AREAS


def to_csv(results: list[dict]) -> bytes:
    """検索結果を CSV（Excelで開けるよう BOM 付き UTF-8）に。"""
    buf = io.StringIO()
    fields = ["area", "title", "type", "author", "timestamp", "url", "excerpt"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in results:
        row = {k: r.get(k, "") for k in fields}
        # 領域キーを表示名に
        area = SEARCH_AREAS.get(r.get("area") or "", {})
        row["area"] = area.get("label", r.get("area") or "")
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


def to_pdf(query: str, results: list[dict]) -> bytes:
    """
    検索結果を PDF に。日本語表示のため、環境にある日本語フォントを探して登録する。
    フォントが見つからない場合は標準フォント（日本語が出ない可能性あり）にフォールバック。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = _register_japanese_font(pdfmetrics, TTFont)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def line(text, size=10, dy=6 * mm, bold=False):
        nonlocal y
        if y < 25 * mm:
            c.showPage()
            y = height - 25 * mm
        c.setFont(font_name, size)
        c.drawString(x, y, text[:90])
        y -= dy

    line(f"社内情報検索 結果  ―  検索ワード：{query}", size=14, dy=9 * mm)
    line(f"出力日時：{datetime.now().strftime('%Y-%m-%d %H:%M')}　／　{len(results)} 件", size=9, dy=8 * mm)

    for i, r in enumerate(results, 1):
        area = SEARCH_AREAS.get(r.get("area") or "", {}).get("label", "")
        line(f"{i}. [{area}] {r.get('title','')}", size=11, dy=6 * mm)
        meta = f"   更新:{(r.get('timestamp','') or '')[:10]}  種別:{r.get('type','')}"
        line(meta, size=8, dy=5 * mm)
        excerpt = r.get("excerpt", "")
        # 抜粋は適当な長さで折り返し
        for chunk in _wrap(excerpt, 56):
            line("   " + chunk, size=9, dy=5 * mm)
        line(f"   {r.get('url','')}", size=7, dy=7 * mm)

    c.save()
    return buf.getvalue()


# ----------------------------------------------------------------------
def _register_japanese_font(pdfmetrics, TTFont) -> str:
    """環境内の日本語TTF/TTCを探して登録。見つからなければ Helvetica。"""
    candidates = [
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # mac
        "C:/Windows/Fonts/meiryo.ttc",                       # windows
        "C:/Windows/Fonts/YuGothR.ttc",
    ]
    for path in candidates:
        try:
            import os
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont("jp", path))
                return "jp"
        except Exception:
            continue
    return "Helvetica"


def _wrap(text: str, width: int):
    """単純な文字数折り返し（日本語は等幅前提のざっくり処理）。"""
    text = text or ""
    return [text[i:i + width] for i in range(0, len(text), width)] or [""]
