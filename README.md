# 社内情報検索ツール（Confluence専用版）

Confluence を検索し、必要な情報へ最短でたどり着くための簡易検索ツール。
既存の Confluence 検索と機能を張り合わず、**簡単さ・見やすさ**に振り切ったコンセプト。

## できること

- フリーワード検索（Confluence 全文検索）
- 業務シーン別プリセット（議事録 / プレスリリース / 用語確認 / 手順 / 外国語資料）
- 5領域の検索範囲を色分けして表示・絞り込み
  - EPS全体 / JP Pub / JP Office（ページ配下を細かく検索）
  - GPD1 / GPD2（スペース全体を大雑把に検索）
- 期間フィルタ（指定なし / 今日 / 7日 / 30日 / 1年。既定は1年）
- **PDF など添付ファイルの中身も検索対象**（Confluence のインデックスを利用）
- 検索結果を **要約する** / **分類する**（任意・Claude API）
- 結果を **PDF / CSV** で出力（手元に保存）

## できないこと（意図的にスコープ外）

- Slack 検索（Confluence 専用のため。Slack は Slack 自身/統合検索に委譲）
- Confluence へのページ書き込み（認証基盤の話＝システム担当案件）
- 利用者ごとの権限制御（検索は設定した PAT の権限で動作。結果はその権限範囲に限る）

## セットアップ

```bash
pip install -r requirements.txt
cp .env.sample .env   # .env を編集して認証情報を設定
streamlit run app.py
```

`.env` を設定しない場合は**モックモード**で起動し、サンプルデータで UI を確認できる。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `app.py` | UI（検索バー・プリセット・範囲フィルタ・結果グリッド・要約/分類/出力） |
| `search_areas.py` | 5領域の定義と CQL 範囲組み立て・領域判定 |
| `confluence_client.py` | Confluence 検索（複数範囲・添付対応・抜粋抽出） |
| `claude_client.py` | 要約・分類（Claude API） |
| `exporters.py` | CSV / PDF 出力 |

## 設計メモ

- 検索は素朴な全文一致（`text ~ "query"`）で確実に動かす。CQL の賢い生成は入れていない。
- 検索範囲は ancestor（配下ツリー）と space（スペース全体）を OR で結合して横断。
- 添付は `type in (page, attachment)` で対象に含め、本文が空で PDF だけのページも拾う。
- 要約・分類は API 呼び出しが走るため、ボタン押下時のみ実行（自動では呼ばない）。

## セキュリティと配布（重要）

社内固有の情報（ページID・スペースキー・ドメイン）と各種キーは、すべてコードの外に置く。

| 情報 | 置き場所 | 公開されるか |
| --- | --- | --- |
| コード（app.py 等） | GitHub リポジトリ | される（社内構造は含まない） |
| PAT・APIキー | .env（ローカル）/ Streamlit Secrets（クラウド） | されない |
| ページID・スペースキー・ドメイン | .env / Streamlit Secrets | されない |

- `.env` は `.gitignore` で除外済み。**絶対に GitHub へ上げない。**
- Streamlit Community Cloud で公開リポジトリ運用をする場合も、キー・社内構造は
  すべて Streamlit の Secrets に記載すれば、コード公開とは分離される。
- 領域の設定は `AREA_*` を `type:value`（例 `ancestor:1234567` / `space:SPACEKEY`）で指定。
