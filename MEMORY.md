# MEMORY.md - 本店経理自動化システム 作業ガイド

**毎回作業開始時に必ずこのファイルを読み込むこと。**

---

## 1. 作業ルール（必須）

1. **整合性の維持**: 機能修正・仕様変更時は `app.py`・`templates/rules_tabs.html` を同じコミットで更新すること。
2. **テスト必須**: コード修正後は必ず `python3 -m py_compile` で構文チェックを実施すること。freee API・Notion APIを使う修正はサンドボックスにトークンがないため実機テスト不可。その場合は **「実機テスト未実施」と明示してからプッシュ**すること。
3. **GitHubプッシュ**: classic PAT（`ghp_`始まり・期限なし）を使用。`git remote set-url origin "https://rikutokosa:{PAT}@github.com/rikutokosa/notion-freee-auto.git"` で設定してからpush。fine-grained PATはcontents=writeが付かないため使用不可。
4. **freee再認証**: 通常不要（Railwayボリュームにトークン永続保存）。トークン失効時のみ https://notion-freee-production.up.railway.app/auth/freee へ案内すること。
5. **プロジェクト設定との整合**: ユーザーの指示がこのファイルの内容と矛盾・不整合な場合は、その都度このファイルの修正を提案すること。

---

## 2. システム基本情報

| 項目 | 内容 |
|---|---|
| アプリURL | https://notion-freee-production.up.railway.app |
| GitHubリポジトリ | https://github.com/rikutokosa/notion-freee-auto |
| ホスティング | Railway Hobbyプラン（main ブランチ push → 自動デプロイ） |
| 通知 | Slack Incoming Webhook（`SLACK_WEBHOOK_URL` 環境変数に設定済み） |
| スケジュール実行 | 毎日12:00 JST（Asia/Tokyo）。**APScheduler**（app.py内蔵）が `_do_scheduled_run` → `_do_payment_alert` を順次実行。Manusスケジューラは無効化済み。 |
| freee事業所ID | 1856949 |
| freeeアプリClient ID | 740864584696172 |
| freeeアプリ管理画面 | https://app.secure.freee.co.jp/developers/applications |
| freee OAuthコールバックURL | https://notion-freee-production.up.railway.app/auth/freee/callback |

> RailwayはSMTPポートをブロックしているためメール通知は使用不可。通知はSlack Webhookのみ。

### Notion DB ID（Railway環境変数に設定済み）

| 変数名 | DB | ID |
|---|---|---|
| `NOTION_DB_ID_HONTEN` | 本店CA成約管理DB | `320a7a34-dbe2-8082-8055-c57f9b8a04bb` |
| `NOTION_DB_ID_PCA` | PCA成約管理DB | `32fa7a34-dbe2-8005-ab91-ff33d64506e0` |

---

## 3. コードだけでは分からない設計判断・外部制約

### Notionフィールド名の注意点
- 返金率フィールドは **「返金料率」**（「返金率」ではない）
- 売上金額は **「税込売上」** フィールドを使用（`tax_entry_method: inclusive`）。なければ「税抜売上」×1.1でフォールバック。

### 振込アラートの除外ルール
- **「銀行連携済（IB振込）」はfreee APIで判別不可**（`payment_status: unsettled`のまま）。IB振込データ送信後にfreeeで「振込依頼済」タグ（ID: 35285961）を手動付与することで翌日以降のアラートから除外される。
- CSS部門の仕訳は振込アラートから除外する（CSS部門IDで判定）。

### 手動対応ケースの設計意図
- **Hitolink**: 外部申請フォームが必要なため自動処理不可（外部制約）。
- **返金率が0%でも返金後入金売上・返金後集客手数料が両方入力されていれば処理続行**（業務判断）。
- **入社済 × 請求不要** → エラーにせずスキップ（正常系として設計）。

### DBプロパティ型の違い（本店CA vs PCA）

| プロパティ | 本店CA | PCA |
|---|---|---|
| 集客経路 | rollup | select |
| 決定企業 | relation | rich_text |
| 求職者 | relation | title |
| 担当 | rollup「担当CA」 | select「担当パートナー」 |
| 仕入決済期日 | formula（スペースなし） | formula（末尾スペースあり） |
