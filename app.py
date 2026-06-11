"""
app.py
社内情報検索ツール（Confluence専用版）

コンセプト：簡単・使い勝手特化。既存Confluence検索と機能を張り合わず、
見やすさ・たどり着きやすさに振り切る。

機能：
  - フリーワード検索（素朴な全文一致）＋ 業務シーン別プリセット
  - 5領域の検索範囲チェックボックス（色分け）
  - 期間フィルタ（指定なし/今日/7日/30日/1年、デフォルト1年）
  - PDF添付の中身も検索（type in (page, attachment)）
  - 2カラムのカードグリッド表示（タイトル主役・ヒット抜粋・右下に原本ボタン）
  - 要約する／分類する（任意・Claude API）
  - PDF / CSV 出力（ローカルに落とす）

起動： streamlit run app.py
"""

import os
from datetime import datetime, timedelta

import streamlit as st
from dotenv import load_dotenv

from search_areas import SEARCH_AREAS, DEFAULT_SELECTED
from confluence_client import ConfluenceClient
from claude_client import ClaudeClient
from exporters import to_csv, to_pdf

load_dotenv()
st.set_page_config(page_title="社内情報検索", page_icon="🔎", layout="wide")


def _has_creds() -> bool:
    return bool(os.getenv("CONFLUENCE_PAT") and os.getenv("CONFLUENCE_BASE_URL"))


# 業務シーン別プリセット（ラベル → 実際の検索ワード）
PRESETS = {
    "📋 議事録を探す": "議事録",
    "🎮 ゲームレビュー・競合": "レビュー 競合",
    "📑 社内規程・ルール": "規程 ルール",
    "🔧 手順・マニュアル": "手順 マニュアル",
    "🌐 外国語資料": "原文 English",
}

PERIODS = {
    "指定なし": None,
    "今日": 0,
    "過去7日": 7,
    "過去30日": 30,
    "過去1年": 365,
}

# ---------------------------------------------------------------- CSS
st.markdown("""
<style>
  :root {
    --border:#e3e8ef; --ink:#1c2530; --ink-soft:#5a6675; --ink-faint:#93a0b0;
    --blue:#2563c9; --blue-soft:#eaf1fc; --hit-bg:#fff3cd; --hit-ink:#7a5b00;
  }
  .stApp { background:#f7f9fb; }
  .block-container { padding-top:2rem; max-width:1200px; }
  /* カード */
  .card { background:#fff; border:1px solid var(--border); border-radius:12px;
          padding:16px 18px; margin-bottom:14px; height:100%;
          display:flex; flex-direction:column; }
  .card-top { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .area-badge { font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; }
  .card-date { font-size:11.5px; color:var(--ink-faint); margin-left:auto; }
  .card-title { font-size:15.5px; font-weight:700; color:var(--ink);
                margin-bottom:8px; line-height:1.45; }
  .card-excerpt { font-size:13px; color:var(--ink-soft); line-height:1.7; flex:1; }
  .card-excerpt mark { background:var(--hit-bg); color:var(--hit-ink);
                       font-weight:600; padding:0 2px; border-radius:3px; }
  .card-foot { display:flex; align-items:center; gap:10px; margin-top:14px;
               padding-top:11px; border-top:1px solid #f0f3f7; }
  .breadcrumb { font-size:11px; color:var(--ink-faint); }
  .open-btn { font-size:12.5px; font-weight:600; color:var(--blue);
              background:var(--blue-soft); padding:7px 14px; border-radius:8px;
              text-decoration:none; margin-left:auto; }
  .type-pdf { font-size:10.5px; color:#b45309; background:#fdeede;
              padding:2px 7px; border-radius:5px; font-weight:600; }
  .foot-note { font-size:12px; color:var(--ink-faint); font-style:italic; margin-top:8px; }
  .summary-box { background:#fff; border:1px solid var(--border); border-left:4px solid var(--blue);
                 border-radius:10px; padding:18px 22px; margin:4px 0 8px;
                 color:var(--ink); font-size:14px; line-height:1.85; white-space:pre-wrap; }
  .summary-box .summary-head { font-size:13px; font-weight:700; color:var(--blue);
                 margin-bottom:8px; letter-spacing:.02em; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- state
st.session_state.setdefault("results", None)
st.session_state.setdefault("last_query", "")
st.session_state.setdefault("summary", None)
st.session_state.setdefault("groups", None)
st.session_state.setdefault("query_input", "")


def _after_date(period_label: str) -> str | None:
    days = PERIODS.get(period_label)
    if days is None:
        return None
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _badge_style(area_key: str) -> str:
    bg, ink = SEARCH_AREAS.get(area_key, {}).get("color", ("#eef0f2", "#5a6675"))
    return f"background:{bg};color:{ink};"


def _highlight(text: str, query: str) -> str:
    """抜粋内の検索語を <mark> で強調（簡易・大文字小文字無視）。"""
    import re, html as _html
    safe = _html.escape(text or "")
    for term in [t for t in query.split() if t.strip()]:
        try:
            safe = re.sub(f"({re.escape(_html.escape(term))})",
                          r"<mark>\1</mark>", safe, flags=re.IGNORECASE)
        except re.error:
            pass
    return safe


def _render_grid(items, query):
    """2カラムのカードグリッドを描画。"""
    for row_start in range(0, len(items), 2):
        cols = st.columns(2)
        for col, r in zip(cols, items[row_start:row_start + 2]):
            with col:
                area_key = r.get("area") or ""
                area_label = SEARCH_AREAS.get(area_key, {}).get("label", r.get("space_key", "—"))
                date = (r.get("timestamp", "") or "")[:10]
                excerpt = _highlight(r.get("excerpt", ""), query)
                pdf_tag = '<span class="type-pdf">PDF</span>' if r.get("type") == "attachment" else ""
                card_html = (
                    '<div class="card">'
                    '<div class="card-top">'
                    f'<span class="area-badge" style="{_badge_style(area_key)}">{area_label}</span>'
                    f'{pdf_tag}'
                    f'<span class="card-date">{date}</span>'
                    '</div>'
                    f'<div class="card-title">{r.get("title","")}</div>'
                    f'<div class="card-excerpt">{excerpt}</div>'
                    '<div class="card-foot">'
                    f'<span class="breadcrumb">{r.get("space_key","")}</span>'
                    f'<a class="open-btn" href="{r.get("url","#")}" target="_blank">原本を開く</a>'
                    '</div>'
                    '</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)


def run_search(query: str, areas: list[str], limit: int, period_label: str,
               conf: ConfluenceClient):
    with st.spinner("検索中…"):
        st.session_state.results = conf.search(
            query.strip(), areas, limit=limit, after=_after_date(period_label),
            include_attachments=True)
        st.session_state.last_query = query.strip()
        st.session_state.summary = None
        st.session_state.groups = None


# ---------------------------------------------------------------- sidebar
auto_mock = not _has_creds()
with st.sidebar:
    st.header("検索範囲")
    mock_mode = st.toggle("モックモード", value=auto_mock,
                          help="ON：APIを叩かずサンプルで動作。認証情報が無い場合は自動ON。")
    base_mode = "mock" if mock_mode else "api"

    selected_areas = []
    for key, area in SEARCH_AREAS.items():
        bg, ink = area["color"]
        checked = st.checkbox(area["label"], value=(key in DEFAULT_SELECTED), key=f"area_{key}")
        if checked:
            selected_areas.append(key)

    st.divider()
    period_label = st.radio("期間", list(PERIODS.keys()), index=4)  # デフォルト 過去1年
    limit = st.slider("最大取得件数", 10, 200, 50, step=10)

    st.divider()
    st.caption("接続状態")
    conf_client = ConfluenceClient(mode=base_mode)
    claude_client = ClaudeClient(mode=base_mode)
    for label, client in [("Confluence", conf_client), ("Claude", claude_client)]:
        ok, msg = client.test_connection()
        st.write(f"{'🟢' if ok else '🔴'} {label}：{msg}")

# ---------------------------------------------------------------- main
st.title("社内情報検索")
st.caption("Confluence を検索し、必要な情報へ最短でたどり着く")

# 検索バー
c1, c2 = st.columns([6, 1])
query = c1.text_input("検索ワード", key="query_input",
                      placeholder="探したい言葉を入力（例：LQA / 就業時間 / プレスリリース）",
                      label_visibility="collapsed")
search_clicked = c2.button("検索", type="primary", use_container_width=True)

# プリセット
st.caption("よく使う検索")
preset_cols = st.columns(len(PRESETS))
preset_clicked = None
for col, (label, kw) in zip(preset_cols, PRESETS.items()):
    if col.button(label, use_container_width=True):
        preset_clicked = kw

# 検索実行
if not selected_areas:
    st.warning("検索範囲を1つ以上選択してください。")
elif search_clicked and query.strip():
    run_search(query, selected_areas, limit, period_label, conf_client)
elif preset_clicked:
    run_search(preset_clicked, selected_areas, limit, period_label, conf_client)
elif search_clicked:
    st.warning("検索ワードを入力してください。")

# ---------------------------------------------------------------- results
results = st.session_state.results
if results is not None:
    n = len(results)
    q = st.session_state.last_query

    bar = st.columns([3, 1, 1, 1, 1])
    bar[0].markdown(f"### {n} 件 見つかりました")
    if bar[1].button("📝 要約する", use_container_width=True, disabled=(n == 0)):
        with st.spinner("要約中…"):
            st.session_state.summary = claude_client.summarize(q, results)
    if bar[2].button("🗂 分類する", use_container_width=True, disabled=(n == 0)):
        with st.spinner("分類中…"):
            st.session_state.groups = claude_client.classify(q, results)
    if n > 0:
        bar[3].download_button("⬇ PDF", to_pdf(q, results),
                               file_name=f"search_{q}.pdf", mime="application/pdf",
                               use_container_width=True)
        bar[4].download_button("⬇ CSV", to_csv(results),
                               file_name=f"search_{q}.csv", mime="text/csv",
                               use_container_width=True)

    # 要約パネル（st.info は地のテーマと同色化して読めないため自前ボックス）
    if st.session_state.summary:
        import html as _html
        safe_summary = _html.escape(st.session_state.summary)
        summary_html = (
            '<div class="summary-box">'
            '<div class="summary-head">📝 要約</div>'
            f'{safe_summary}'
            '</div>'
        )
        st.markdown(summary_html, unsafe_allow_html=True)

    st.divider()

    # ゼロ件
    if n == 0:
        st.markdown(
            "**該当するページが見つかりませんでした。**　"
            "別の言葉で試すか、検索範囲を広げてみてください。"
        )
    else:
        # 分類ビュー or 通常グリッド
        groups = st.session_state.groups
        if groups:
            for gname, idxs in groups.items():
                st.markdown(f"#### ▤ {gname}（{len(idxs)}件）")
                _render_grid([results[i] for i in idxs if 0 <= i < n], q)
        else:
            _render_grid(results, q)

    st.markdown(
        '<div class="foot-note">※ 検索結果は、設定したConfluence権限で閲覧できるページに限られます。'
        'PDFなど添付ファイルの中身も検索対象に含まれます。</div>',
        unsafe_allow_html=True)



