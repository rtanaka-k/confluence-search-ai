"""
search_areas.py
検索範囲（5領域）の定義。

各領域は2つのタイプのいずれか：
  - "ancestor" : 指定ページ配下のツリーを検索（細かく検索する領域）
  - "space"    : スペース全体を検索（大雑把に検索する領域）

【セキュリティ方針】
社内固有の情報（ページID・スペースキー・ドメイン）はコードに直書きせず、
環境変数（.env / Streamlit Secrets）から読み込む。
これにより、コードを公開リポジトリに置いても社内構造が露出しない。
コードに残すのは、秘密でない情報（表示名・配色）のみ。

環境変数の形式（.env / Secrets に記載）：
  CONFLUENCE_BASE_URL=https://your-domain.atlassian.net/wiki
  AREA_EPS=ancestor:1234567        # type:value
  AREA_JP_PUB=ancestor:1234567
  AREA_JP_OFFICE=ancestor:1234567
  AREA_GPD1=space:SPACEKEY
  AREA_GPD2=space:SPACEKEY
"""

import os

# --- 秘密でない表示用メタ（コードに残してよい） ---
_AREA_META = {
    "eps":       {"label": "EPS全体",   "color": ("#e7eefc", "#1d4ed8"), "env": "AREA_EPS"},
    "jp_pub":    {"label": "JP Pub",    "color": ("#e3f4ea", "#15803d"), "env": "AREA_JP_PUB"},
    "jp_office": {"label": "JP Office", "color": ("#fdeede", "#b45309"), "env": "AREA_JP_OFFICE"},
    "gpd1":      {"label": "GPD1",      "color": ("#f0e9fb", "#7c3aed"), "env": "AREA_GPD1"},
    "gpd2":      {"label": "GPD2",      "color": ("#dff3f3", "#0d7d7d"), "env": "AREA_GPD2"},
}

# モックモード用ダミー値（実際の社内IDではない）。環境変数が無いデモ時のUI成立用。
_MOCK_VALUES = {
    "eps":       ("ancestor", "000000001"),
    "jp_pub":    ("ancestor", "000000002"),
    "jp_office": ("ancestor", "000000003"),
    "gpd1":      ("space", "MOCKGPD1"),
    "gpd2":      ("space", "MOCKGPD2"),
}


def _base_url() -> str:
    return os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/")


def _build_url(area_type: str, value: str) -> str:
    """type と value から領域の入口URLを組み立てる（ベースURLは環境変数）。"""
    base = _base_url()
    if not base or not value:
        return base or "#"
    if area_type == "ancestor":
        return f"{base}/pages/viewpage.action?pageId={value}"
    if area_type == "space":
        return f"{base}/spaces/{value}/overview"
    return base


def _load_areas() -> dict:
    """環境変数から type:value を読み込み SEARCH_AREAS を構築。無ければモック値。"""
    areas = {}
    for key, meta in _AREA_META.items():
        raw = os.getenv(meta["env"], "")
        if raw and ":" in raw:
            area_type, value = raw.split(":", 1)
            area_type, value = area_type.strip(), value.strip()
        else:
            area_type, value = _MOCK_VALUES[key]
        areas[key] = {
            "label": meta["label"],
            "type": area_type,
            "value": value,
            "color": meta["color"],
            "url": _build_url(area_type, value),
        }
    return areas


SEARCH_AREAS = _load_areas()
DEFAULT_SELECTED = list(SEARCH_AREAS.keys())


def build_scope_cql(selected_keys: list[str]) -> str:
    """選択領域から CQL 範囲断片を組み立て。ancestor in (...) OR space in (...)。"""
    ancestors, spaces = [], []
    for key in selected_keys:
        area = SEARCH_AREAS.get(key)
        if not area:
            continue
        if area["type"] == "ancestor":
            ancestors.append(area["value"])
        elif area["type"] == "space":
            spaces.append(area["value"])

    clauses = []
    if ancestors:
        clauses.append(f"ancestor in ({', '.join(ancestors)})")
    if spaces:
        clauses.append(f"space in ({', '.join(spaces)})")

    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def area_of_result(space_key: str, ancestor_ids: list[str]) -> str:
    """space_key と祖先IDから領域を判定。ancestor優先。判定不能なら None。"""
    for key, area in SEARCH_AREAS.items():
        if area["type"] == "ancestor" and area["value"] in ancestor_ids:
            return key
    for key, area in SEARCH_AREAS.items():
        if area["type"] == "space" and area["value"] == space_key:
            return key
    return None
