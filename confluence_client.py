"""
confluence_client.py
Confluence 検索クライアント（Confluence専用版）

前バージョン（単一ancestor固定）からの変更点：
  - 複数領域の横断検索に対応（search_areas.build_scope_cql を使用）
  - type=page だけでなく type=attachment も対象（PDF添付の中身を拾う）
  - ヒット箇所抜粋（excerpt）を検索APIから取得して結果に含める
  - スペースキー・祖先から領域（5領域のどれか）を判定して付与

返却 dict の共通キー：
  source / title / excerpt / area / space_key / type / author / timestamp / url / page_id

検索は利用者ごとの権限制御は行わず、設定された PAT の権限で動く。
（結果は PAT 保有者が閲覧できる範囲に限られる旨を UI で注記する）
"""

import os
import re
import html
import requests
from requests.auth import HTTPBasicAuth

from search_areas import build_scope_cql, area_of_result, SEARCH_AREAS


class ConfluenceClient:
    def __init__(self, mode: str = "mock", base_url=None, email=None, pat=None):
        self.mode = mode
        self.base_url = (base_url or os.getenv("CONFLUENCE_BASE_URL", "")).rstrip("/")
        self.email = email or os.getenv("CONFLUENCE_EMAIL", "")
        self.pat = pat or os.getenv("CONFLUENCE_PAT", "")
        self._auth = HTTPBasicAuth(self.email, self.pat) if self.email and self.pat else None

    # ------------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        if self.mode == "mock":
            return True, "モックモード（サンプルデータで動作）"
        if not self._auth or not self.base_url:
            return False, "認証情報（BASE_URL / EMAIL / PAT）が未設定です"
        try:
            resp = requests.get(
                f"{self.base_url}/rest/api/space",
                auth=self._auth, params={"limit": 1}, timeout=10,
            )
            return (resp.status_code == 200,
                    "接続成功" if resp.status_code == 200 else f"接続失敗：HTTP {resp.status_code}")
        except requests.RequestException as e:
            return False, f"接続エラー：{e}"

    # ------------------------------------------------------------------
    def search(self, query: str, selected_areas: list[str],
               limit: int = 50, after: str | None = None,
               include_attachments: bool = True) -> list[dict]:
        """
        query          : 検索ワード（素朴な全文一致 text ~ "query"）
        selected_areas : 検索範囲の領域キー（search_areas のキー）
        limit          : 取得上限
        after          : "YYYY-MM-DD"。指定時は lastmodified >= after
        include_attachments : True なら type in (page, attachment)、False なら page のみ
        """
        if self.mode == "mock":
            return _mock_results(query, selected_areas, limit)

        if not self._auth or not self.base_url:
            raise RuntimeError("Confluence 認証情報が未設定です")

        cql = self._build_cql(query, selected_areas, after, include_attachments)

        resp = requests.get(
            f"{self.base_url}/rest/api/search",
            auth=self._auth,
            params={
                "cql": cql,
                "limit": limit,
                # excerpt（ヒット箇所抜粋）を取得。ancestors/space は領域判定用。
                "expand": "content.space,content.ancestors,content.version",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items = [self._normalize(r) for r in data.get("results", [])]
        # Confluence REST の検索は relevance スコアを返さない（全件 score:0）。
        # 素の並びはほぼ更新日順。検索ツールとして「タイトルに語があるものを
        # 確実に上へ」だけを簡易スコアで実現し、同点は元の順（新しい順）を保つ。
        # 本格的な関連度ランキング（同義語・語幹・TF-IDF）は Rovo の領域なので
        # ここでは追わない（キックオフでの自作 vs Rovo の判断材料のひとつ）。
        scored = [(self._relevance(it, query), i, it) for i, it in enumerate(items)]
        # スコア降順、同点は元の並び順（i 昇順）を維持
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [it for _, _, it in scored]

    @staticmethod
    def _relevance(item: dict, query: str) -> float:
        """簡易関連度スコア。タイトル一致のみを主軸に「上へ」拾い上げる。

        Confluence API が relevance を返さないための最小限の補完。
        本文（excerpt）の出現はごく軽い味付けに留め、暴れないようにする。
        """
        title = (item.get("title", "") or "").lower()
        excerpt = (item.get("excerpt", "") or "").lower()
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return 0.0
        score = 0.0
        for t in terms:
            if t in title:
                score += 10.0              # タイトル一致が主軸
            if t in excerpt:
                score += 0.5               # 本文は「含むか否か」だけ軽く加点
        if all(t in title for t in terms):
            score += 5.0                   # 全語タイトル一致はボーナス
        return score

    # ------------------------------------------------------------------
    def _build_cql(self, query: str, selected_areas: list[str],
                   after: str | None, include_attachments: bool) -> str:
        # 検索語はダブルクオートをエスケープ
        safe_q = query.replace('"', '\\"')
        parts = [f'text ~ "{safe_q}"']

        scope = build_scope_cql(selected_areas)
        if scope:
            parts.append(scope)

        if include_attachments:
            parts.append("type in (page, attachment)")
        else:
            parts.append("type = page")

        if after:
            parts.append(f'lastmodified >= "{after}"')

        # ORDER BY を付けると Confluence は relevance スコアリングをやめ
        # 指定順で返す（全件 score:0 になる）。検索ツールとしては
        # 関連度順が直感に合うため、ORDER BY は付けずデフォルト（関連度順）に委ねる。
        # 期間の絞り込みは上の lastmodified フィルタが担うので、並びと絞り込みは独立。
        cql = " AND ".join(parts)
        return cql

    # ------------------------------------------------------------------
    def _normalize(self, raw: dict) -> dict:
        """検索APIの1結果を共通フォーマットに整える。"""
        content = raw.get("content", {})
        space_key = content.get("space", {}).get("key", "")
        ancestors = [str(a.get("id", "")) for a in content.get("ancestors", [])]
        area_key = area_of_result(space_key, ancestors)

        # excerpt は検索APIが返すヒット箇所抜粋。HTMLタグやエンティティを除去。
        excerpt = _clean_excerpt(raw.get("excerpt", ""))

        webui = raw.get("url") or content.get("_links", {}).get("webui", "")
        url = f"{self.base_url}{webui}" if webui.startswith("/") else webui

        version = content.get("version", {})
        return {
            "source": "Confluence",
            "page_id": content.get("id", ""),
            "title": content.get("title", ""),
            "excerpt": excerpt,
            "area": area_key,                       # 5領域キー or None
            "space_key": space_key,
            "type": content.get("type", ""),        # page / attachment
            "author": version.get("by", {}).get("displayName", ""),
            "timestamp": raw.get("lastModified", "") or version.get("when", ""),
            "url": url,
        }


# ----------------------------------------------------------------------
# ヘルパー
# ----------------------------------------------------------------------
def _clean_excerpt(text: str) -> str:
    """excerpt の HTML タグ・エンティティ・余分な空白を除去して読みやすく。"""
    if not text:
        return ""
    # @@@hl@@@ ... @@@endhl@@@ のようなハイライトマーカーが入ることがある → 除去
    text = text.replace("@@@hl@@@", "").replace("@@@endhl@@@", "")
    text = re.sub(r"<[^>]+>", "", text)        # HTMLタグ除去
    text = html.unescape(text)                  # &amp; などをデコード
    text = re.sub(r"\s+", " ", text).strip()    # 連続空白を1つに
    return text


def _mock_results(query: str, selected_areas: list[str], limit: int) -> list[dict]:
    """APIキー無しでもUI確認できるサンプル。選択領域に応じてそれっぽく返す。"""
    samples = [
        ("eps", "LQA 実施手順マニュアル", "page",
         f"対象言語のテストケースを準備します。{query}では表示崩れ・文字切れ・誤訳を重点的に確認し…",
         "山田 太郎", "2026-05-20T10:00:00"),
        ("jp_pub", "LQA チェックリスト", "page",
         f"固有名詞の表記が用語集と一致しているか。リリース前の{query}最終確認に使うチェック項目の一覧です…",
         "佐藤 花子", "2026-05-18T14:30:00"),
        ("jp_office", "就業規則&賃金規程", "attachment",
         f"第3章 勤務の基準。{query}および休憩・休日について定める。所定労働時間は…（PDF添付）",
         "HR Part", "2026-01-01T09:00:00"),
        ("gpd1", "翻訳ワークフロー概要", "page",
         f"原文受領 → 翻訳 → レビュー → {query} → 最終承認 の順で進行します…",
         "鈴木 次郎", "2026-04-30T09:15:00"),
        ("gpd2", "[RHL] L10N テスト記録", "page",
         f"各言語版ビルドに対する{query}の実施記録。検出バグの一覧と対応状況を管理…",
         "Park", "2026-03-22T11:05:00"),
    ]
    results = []
    for area_key, title, ctype, excerpt, author, ts in samples:
        if area_key not in selected_areas:
            continue
        area = SEARCH_AREAS[area_key]
        results.append({
            "source": "Confluence",
            "page_id": f"mock-{area_key}",
            "title": title,
            "excerpt": excerpt,
            "area": area_key,
            "space_key": area["value"] if area["type"] == "space" else "GPS",
            "type": ctype,
            "author": author,
            "timestamp": ts,
            "url": area["url"],
        })
    return results[:limit]
