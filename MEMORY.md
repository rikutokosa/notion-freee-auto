# MEMORY.md - 本店経理自動化システム 作業ガイド

**毎回作業開始時に必ずこのファイルを読み込むこと。コード修正時はさらに `SYSTEM_SPEC.md` も参照すること。**

---

## 1. 作業ルール（必須）

1. **整合性の維持**: 機能修正・仕様変更時は `app.py`・`templates/`・`SYSTEM_SPEC.md`・このファイルをすべて同じコミットで更新すること。一部だけ修正して他を放置することは禁止。
2. **プロジェクト設定との整合**: ユーザーの指示がこのファイルの内容と矛盾・不整合な場合は、その都度このファイルの修正を提案すること。
3. **作業完了後**: このMEMORY.mdをリポジトリとManusプロジェクト共有ファイルの両方に反映すること。
4. **GitHubプッシュ**: classic PAT（`ghp_`始まり・期限なし）を使用。`git remote set-url origin "https://rikutokosa:{PAT}@github.com/rikutokosa/notion-freee-auto.git"` で設定してからpush。fine-grained PATはcontents=writeが付かないため使用不可。
5. **freee再認証**: 通常不要（Railwayボリュームにトークン永続保存）。トークン失効時のみ https://notion-freee-production.up.railway.app/auth/freee へ案内すること。
6. **テスト必須**: コード修正後は必ず `python3 -m py_compile` で構文チェックを実施すること。freee API・Notion APIを使う修正はサンドボックスにトークンがないため実機テスト不可。その場合は **「実機テスト未実施・Railway環境での動作確認が必要」と明示してからプッシュ**すること。テスト可能なもの（構文・ロジック・モックデータでの動作確認）は必ず実施してから返すこと。

---

## 2. システム基本情報

| 項目 | 内容 |
|---|---|
| アプリURL | https://notion-freee-production.up.railway.app |
| GitHubリポジトリ | https://github.com/rikutokosa/notion-freee-auto |
| ホスティング | Railway（main ブランチ push → 自動デプロイ） |
| 通知 | Slack Incoming Webhook（`SLACK_WEBHOOK_URL` 環境変数に設定済み） |
| スケジュール実行 | 毎日12:00 JST（Asia/Tokyo）。**APScheduler**（app.py内蔵）が `_do_scheduled_run` → `_do_payment_alert` を順次実行。Manusスケジューラは無効化済み。 |
| freee事業所ID | 1856949 |

> RailwayはSMTPポートをブロックしているためメール通知は使用不可。通知はSlack Webhookのみ。

---

## 3. 設計上の重要な判断（コードを読んでも意図が分かりにくい箇所）

### Notionフィールド名の注意点
- 返金率フィールドは **「返金料率」**（「返金率」ではない）
- 売上金額は **「税込売上」** フィールドを使用（`tax_entry_method: inclusive`）。なければ「税抜売上」×1.1でフォールバック。
- `freee売上取引ID`・`freee支出取引ID` = freeeの取引先ID（partner_id）。名前検索ではなくIDを直接使用。

### 振込アラートの除外ルール
- `payment_status: settled`（決済済み）→ 自動除外
- 「振込依頼済」メモタグ（ID: 35285961）が付いている → 自動除外
- **「銀行連携済（IB振込）」はAPIで判別不可**（`unsettled`のまま）。IB振込データ送信後にfreeeで「振込依頼済」タグを手動付与することで翌日以降のアラートから除外される。

### スキップ・手動対応の設計意図
- 入社済 × 請求不要 → エラーにせずスキップ（ダッシュボードに表示しない）
- 手動対応ケース（自動処理をスキップしてNotionに「要確認」と記録）の詳細は `RULEBOOK.md` セクション7を参照。

### DBプロパティ型の違い（本店CA vs PCA）
| プロパティ | 本店CA | PCA |
|---|---|---|
| 集客経路 | rollup | select |
| 決定企業 | relation | rich_text |
| 求職者 | relation | title |
| 担当 | rollup「担当CA」 | select「担当パートナー」 |
| 仕入決済期日 | formula（スペースなし） | formula（末尾スペースあり） |

---

## 4. 仕訳ルール・業務仕様

詳細は **`RULEBOOK.md`** を参照すること。MEMORY.mdには重複して記載しない。

---
