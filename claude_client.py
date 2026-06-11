"""
claude_client.py
Claude API ラッパー（要約・分類）

役割：
  - summarize() : 検索結果全体を要約する（「要約する」ボタン）
  - classify()  : 検索結果を種類別にグルーピングする（「分類する」ボタン）

いずれも任意操作（ボタン押下時のみ呼ぶ）。通常の一覧表示では呼ばない。
モックモード時は API を叩かずサンプルを返す。
"""

import os
import json
import re

MODEL = "claude-sonnet-4-20250514"


class ClaudeClient:
    def __init__(self, mode: str = "mock", api_key: str | None = None):
        self.mode = mode
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None
        if self.mode != "mock" and self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                self._client = None

    # ------------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        if self.mode == "mock":
            return True, "モックモード（要約・分類はサンプル）"
        if not self._client:
            return False, "anthropic SDK 未導入、または API キー未設定です"
        try:
            self._client.messages.create(
                model=MODEL, max_tokens=16,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True, "接続成功"
        except Exception as e:
            return False, f"接続エラー：{e}"

    # ------------------------------------------------------------------
    def _corpus(self, results: list[dict]) -> str:
        """検索結果を要約・分類の材料テキストに整形。"""
        lines = []
        for i, r in enumerate(results):
            lines.append(
                f"[{i}] {r.get('title','')}（{r.get('timestamp','')[:10]}）: "
                f"{r.get('excerpt','')[:300]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def summarize(self, query: str, results: list[dict]) -> str:
        """検索結果全体を、質問に答える形で要約。"""
        if self.mode == "mock" or not self._client:
            return _mock_summary(query, results)

        system = (
            "あなたは社内情報検索の補助です。以下の検索結果全体を、"
            "ユーザーの検索意図に答える形で要約してください。"
            "要点を3つ程度の箇条書きにし、最後に一言で結論（どれを見れば良いか）を述べてください。"
            "検索結果に無いことは推測で補わないでください。"
        )
        try:
            resp = self._client.messages.create(
                model=MODEL, max_tokens=1024, system=system,
                messages=[{"role": "user",
                           "content": f"検索ワード：「{query}」\n\n検索結果:\n{self._corpus(results)}"}],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        except Exception as e:
            return f"要約に失敗しました：{e}"

    # ------------------------------------------------------------------
    def classify(self, query: str, results: list[dict]) -> dict:
        """
        検索結果を内容の種類でグルーピングする。
        返り値：{ "グループ名": [結果インデックス, ...], ... }
        """
        if self.mode == "mock" or not self._client:
            return _mock_classify(results)

        system = (
            "あなたは社内情報検索の補助です。以下の検索結果を、内容の種類で2〜5個のグループに分類してください。"
            "グループ名は『手順・マニュアル』『チェックリスト』『規程・ルール』『議事録・記録』『その他』のように"
            "内容の性質を表す簡潔な日本語にしてください。"
            "出力は次のJSON形式のみ（前後に説明文やコードフェンスを付けない）："
            '{"グループ名": [対象の番号の配列], ...}'
        )
        try:
            resp = self._client.messages.create(
                model=MODEL, max_tokens=1024, system=system,
                messages=[{"role": "user",
                           "content": f"検索ワード：「{query}」\n\n検索結果:\n{self._corpus(results)}"}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            parsed = _extract_json_object(text)
            return parsed if isinstance(parsed, dict) else _mock_classify(results)
        except Exception:
            return _mock_classify(results)


# ----------------------------------------------------------------------
# ヘルパー
# ----------------------------------------------------------------------
def _extract_json_object(text: str):
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # 最初の { から最後の } までを試す
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
        return None


def _mock_summary(query: str, results: list[dict]) -> str:
    n = len(results)
    return (
        f"（モック要約）検索ワード「{query}」について {n} 件が見つかりました。\n"
        "・手順やチェックリストなど、実務に使える資料が中心です。\n"
        "・規程類はPDF添付として登録されているものがあります。\n"
        "・結論：まず上位のタイトルを確認し、目的に近いものを開いてください。"
    )


def _mock_classify(results: list[dict]) -> dict:
    """種類が判別しづらいモックでは、type と簡単なキーワードで雑にグルーピング。"""
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(results):
        title = r.get("title", "")
        if r.get("type") == "attachment" or "規程" in title or "規則" in title:
            g = "規程・ルール"
        elif "チェック" in title:
            g = "チェックリスト"
        elif "手順" in title or "マニュアル" in title or "ワークフロー" in title:
            g = "手順・マニュアル"
        elif "記録" in title or "議事" in title:
            g = "議事録・記録"
        else:
            g = "その他"
        groups.setdefault(g, []).append(i)
    return groups
