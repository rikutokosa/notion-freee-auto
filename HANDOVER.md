# Notion→freee 自動仕訳登録アプリ 引き継ぎ情報

作成日: 2026-06-16  
最終更新: 2026-06-29

---

## システム概要

NotionのCA成約管理DB（本店CA・PCA）を監視し、「②経理対応待ち」ステータスのレコードを自動的にfreee会計に仕訳登録するWebアプリ。毎日12:00 JSTに自動実行され、結果はSlackに通知される。

---

## アクセス情報

| 項目 | 値 |
|---|---|
| **アプリURL** | https://notion-freee-production.up.railway.app |
| **GitHubリポジトリ** | https://github.com/rikutokosa/notion-freee-auto |
| **GitHubユーザー** | rikutokosa |
| **Railwayプロジェクト** | notion-freee-auto（rikutokosaアカウント） |
| **freeeアプリ名** | notion 自動転記 |
| **freeeアプリClient ID** | 740864584696172 |

---

## 環境変数（Railway Variables に設定済み）

| 変数名 | 内容 | 状態 |
|---|---|---|
| `NOTION_TOKEN` | Notionインテグレーションのシークレットトークン | 使用中 |
| `NOTION_DB_ID_HONTEN` | 本店CA成約管理DBのID: `320a7a34-dbe2-8082-8055-c57f9b8a04bb` | 使用中 |
| `NOTION_DB_ID_PCA` | PCA成約管理DBのID: `32fa7a34-dbe2-8005-ab91-ff33d64506e0` | 使用中 |
| `FREEE_CLIENT_ID` | freeeアプリのClient ID | 使用中 |
| `FREEE_CLIENT_SECRET` | freeeアプリのClient Secret | 使用中 |
| `FREEE_COMPANY_ID` | freee事業所ID（ベアーズナビ株式会社）: `1856949` | 使用中 |
| `APP_BASE_URL` | `https://notion-freee-production.up.railway.app` | 使用中 |
| `SECRET_KEY` | Flaskセッション用シークレットキー | 使用中 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL（スケジュール実行結果の通知先） | **使用中** |
| `FREEE_AUTO_STOPPED` | `1`（バックグラウンドポーリング停止中） | 使用中 |
| `NOTIFY_TO` | `rikuto.kosa@gmail.com`（現在未使用） | 未使用 |
| `RESEND_API_KEY` | Resend APIキー（現在未使用） | 未使用 |
| `SMTP_USER` | `bearsnavi.sidesales@gmail.com`（現在未使用） | 未使用 |

> **注意**: RailwayはSMTPポート（587・465）を両方ブロックしているため、メール送信は使用不可。通知はSlack Webhookのみ。

---

## コードファイル構成

| ファイル | 役割 |
|---|---|
| `app.py` | Flaskアプリ本体。ルーティング・freee OAuth・スケジュール実行・Slack通知・仕訳アシスタント |
| `notion_client.py` | Notion APIクライアント。DBクエリ・求職者名取得・ステータス更新 |
| `freee_client.py` | freee APIクライアント。OAuth・仕訳登録・請求書登録・部門取得・振込対象取得 |
| `rules.py` | 仕訳ルールエンジン。集客経路→取引先・勘定科目・決済期日を決定 |
| `processor.py` | 処理エンジン。rulesの結果をもとにfreeeへ登録 |
| `matcher.py` | freee取引とNotionデータのマッチング処理（書類照合） |
| `payment.py` | 振込アラートの判定ロジック・全銀ファイル生成 |
| `templates/` | HTMLテンプレート（ダッシュボード・プレビュー） |

---

## APIエンドポイント一覧

| エンドポイント | メソッド | 役割 |
|---|---|---|
| `/` | GET | ダッシュボード |
| `/preview` | GET | 経理対応待ちプレビュー |
| `/log` | GET | 処理ログ一覧 |
| `/auth/freee` | GET | freee OAuth認証開始 |
| `/auth/freee/callback` | GET | freee OAuth認証コールバック |
| `/run` | POST | 手動で全件処理 |
| `/run/single` | POST | 1件処理 |
| `/api/pending` | GET | 経理対応待ち一覧（JSON） |
| `/api/logs` | GET | 処理ログ（JSON） |
| `/api/status` | GET | システム状態（JSON） |
| `/api/execution_logs` | GET | 実行ログ（SQLite永続）（JSON） |
| `/api/refresh_cache` | POST | freeeマスタキャッシュ更新 |
| `/api/match/preview` | GET | 書類照合プレビュー |
| `/api/match/execute` | POST | 書類照合実行 |
| `/api/assistant/ai` | POST | 仕訳アシスタント（OpenAI Function Calling） |
| `/api/assistant/register` | POST | フォームから直接freeeに仕訳登録 |
| `/api/scheduled_run` | POST | **スケジュール実行**（自動転記＋書類照合＋Slack通知） |
| `/api/payment_alert` | POST | **振込アラート**（支払期日5日以内の未処理取引をSlack通知） |
| `/api/get_refresh_token` | GET | リフレッシュトークン取得（Railway環境変数設定用） |

---

## スケジュール実行

毎日12:00 JST（03:00 UTC）に以下の順序で自動実行される。

1. **`/api/scheduled_run`**: 自動転記（Notion→freee）＋書類照合を実行し、結果をSlackに通知
2. **`/api/payment_alert`**: 支払期日5日以内の振込未処理取引を検出し、Slackに通知

スケジュールは `manus-config` で管理されている（task-uid: AXQeZ）。

---

## 通知方式（Slack Webhook）

`send_notification_email()` 関数がSlack Incoming Webhookによる通知を担う。

- 環境変数 `SLACK_WEBHOOK_URL` にWebhook URLを設定する
- メッセージ形式: `*件名*\n\`\`\`本文\`\`\``（コードブロック）
- RailwayのSMTPポートブロックのため、メール通知は使用不可（2026-06-26移行）

---

## データベース（SQLite）

Railwayボリューム（`/data/chat_history.db`）に永続化されている。

| テーブル | 用途 |
|---|---|
| `chat_sessions` | チャット会話履歴 |
| `rules_notes` | 運用ルール・メモ |
| `execution_logs` | 実行ログ（自動転記・書類照合・振込アラート） |

---

## 仕訳ルール（rules.py の RULES 定義）

### 求人データベース（売上の取引先になる）

| Notionの値 | 取引先 | 決済期日 | 請求書 |
|---|---|---|---|
| Circus \| 請求不要 | circus株式会社 | 入社翌々月10日 | 不要 |
| Zキャリア \| 請求書不要 | 株式会社ROXX | 入社翌々月10日 | 不要 |
| クラウドエージェント│請求不要 | 株式会社Grooves | 入社翌々月4日 | 不要 |
| マイナビJOBシェアリング | 株式会社マイナビ | 入社翌月末 | 必要 |
| Bee | 株式会社ネオキャリア | 入社翌月末 | 必要 |
| CSS自社求人│スカウト手数料のみ登録 | なし | 登録不要 | 不要 |
| CSS自社求人│請求不要 | なし | 登録不要 | 不要 |
| 本店自社求人 | なし | 都度確認 | 必要 |
| Hitolink | なし | 入社翌月末 | 申請フォーム |

### 集客経路（仕入の取引先になる）

| Notionの値 | 取引先 | 決済期日 |
|---|---|---|
| RDS | 株式会社インディードリクルートパートナーズ | 入社翌々月末日 |
| マイナビ転職 | 株式会社マイナビ | 入社翌々月10日 |
| dodaX | パーソルキャリア株式会社 | 入社翌月末 |
| キミナラ | 株式会社キミナラ | 入社翌月末 |
| ワンキャリア | 株式会社ワンキャリア | 入社翌月末 |
| openwork | なし | 入社翌月末 |
| tezuna | なし | 入社翌月末 |

---

## 仕訳の構造

### 本店CA（honten）の場合

**売上仕訳（収入）**
- 発生日: 成約日（なければ入社日）
- 取引先: 求人データベースの取引先（求人DB型のみ）
- 勘定科目: `CA売上【自社】`
- 決済期日: Notionの「売上決済期日」（なければ自動計算）
- 部門: `本店CA`
- 備考: `担当CA名 求職者名 フェーズ`

**仕入仕訳（支出）**
- 発生日: 入社日
- 取引先: 集客経路のルールから取得
- 勘定科目: `スカウト手数料`
- 決済期日: 自動計算（集客経路の支払ルールに基づく）
- 部門: `本店CA`
- 備考: `担当CA名 求職者名 フェーズ`

### PCA成約管理（pca）の場合

**売上仕訳（収入）**
- 勘定科目: `PCA売上`
- 部門: `本店PCA`

**仕入仕訳（支出）**
- 勘定科目: `スカウト手数料`
- 部門: `本店PCA`

**PCA仕入仕訳（支出）**
- 発生日: 入社日
- 取引先: 担当パートナー名（Notionの「担当パートナー」プロパティから取得）
- 勘定科目: `PCA仕入高`
- 決済期日: 入社翌々月末日
- 部門: `本店PCA`

---

## 操作方法

### アプリの使い方
1. https://notion-freee-production.up.railway.app にアクセス
2. **仕訳プレビュー**: 登録前に内容を確認できる
3. **全件処理（本店CA+PCA）**: 全件を一括でfreeeに登録
4. **本店CAのみ / PCAのみ**: 個別に処理
5. スケジュール実行: 毎日12:00 JSTに自動実行（手動停止不要）

### コードを修正する場合
1. `/home/ubuntu/notion-freee-auto/` のコードを編集
2. `git add -A && git commit -m "修正内容" && git push origin main`
3. Railwayが自動デプロイ（1〜2分）
4. **デプロイ後は必ずfreee再認証が必要**: https://notion-freee-production.up.railway.app/auth/freee

### freee認証が切れた場合
- https://notion-freee-production.up.railway.app/auth/freee にアクセスして再認証

---

## freeeアプリ設定

- **コールバックURL**: `https://notion-freee-production.up.railway.app/auth/freee/callback`
- **スコープ**: `read write`
- **管理画面**: https://app.secure.freee.co.jp/developers/applications

---

## 残課題・注意事項

### 1. Slack通知チャンネルの確認
- Slack Webhookは `ok` を返しているが、通知が届くチャンネルの確認が必要
- Webhook URL: `https://hooks.slack.com/services/T096E7J8QN6/B0BE7NYSUM6/...`

### 2. freee再認証（デプロイのたびに必要）
- Railwayのボリュームにトークンが保存されているが、デプロイ後は再認証が必要な場合がある
- 再認証URL: https://notion-freee-production.up.railway.app/auth/freee

### 3. PCA担当パートナー名の取得
- Notionの「担当パートナー」プロパティ（relation or select）から取得する実装が必要

### 4. Railwayの課金
- 無料枠を超えた場合は https://railway.app/account/billing でクレジットカード登録（月$5程度）
