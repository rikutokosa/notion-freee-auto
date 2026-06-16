# Notion→freee 自動仕訳登録アプリ 引き継ぎ情報

作成日: 2026-06-16

---

## システム概要

NotionのCA成約管理DBを定期監視し、「②経理対応待ち」ステータスのレコードを自動的にfreee会計に仕訳登録するWebアプリ。

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

| 変数名 | 内容 |
|---|---|
| `NOTION_TOKEN` | Notionインテグレーションのシークレットトークン |
| `NOTION_DB_ID_HONTEN` | 本店CA成約管理DBのID: `320a7a34-dbe2-8082-8055-c57f9b8a04bb` |
| `NOTION_DB_ID_PCA` | PCA成約管理DBのID: `32fa7a34-dbe2-8005-ab91-ff33d64506e0` |
| `FREEE_CLIENT_ID` | freeeアプリのClient ID |
| `FREEE_CLIENT_SECRET` | freeeアプリのClient Secret |
| `FREEE_COMPANY_ID` | freee事業所ID（ベアーズナビ株式会社） |
| `APP_BASE_URL` | `https://notion-freee-production.up.railway.app` |
| `SECRET_KEY` | Flaskセッション用シークレットキー |

---

## コードファイル構成

| ファイル | 役割 |
|---|---|
| `app.py` | Flaskアプリ本体。ルーティング・freee OAuth・ポーリング制御 |
| `notion_client.py` | Notion APIクライアント。DBクエリ・求職者名取得・ステータス更新 |
| `freee_client.py` | freee APIクライアント。OAuth・仕訳登録・請求書登録・部門取得 |
| `rules.py` | 仕訳ルールエンジン。集客経路→取引先・勘定科目・決済期日を決定 |
| `processor.py` | 処理エンジン。rulesの結果をもとにfreeeへ登録 |
| `templates/` | HTMLテンプレート（ダッシュボード・プレビュー） |

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

### PCA（パートナー）

| Notionの値 | 取引先 | 決済期日 |
|---|---|---|
| PCA | 担当パートナー名（要実装） | 入社翌々月末日 |

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
- 取引先: **集客経路**のルールから取得（RDS→株式会社インディードリクルートパートナーズ等）
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
- 取引先: **担当パートナー名**（Notionの「担当パートナー」プロパティから取得 → 未実装）
- 勘定科目: `PCA仕入高`
- 決済期日: 入社翌々月末日
- 部門: `本店PCA`

---

## Notionのプロパティ対応

| Notionプロパティ名 | 取得方法 | 用途 |
|---|---|---|
| フェーズ | title | 備考に含める |
| 担当CA | rollup（select） | 備考に含める |
| 求人データベース | select | 仕訳ルール判定・売上取引先 |
| 集客経路 | rollup（select） | 仕入取引先 |
| 請求ステータス | select | 処理トリガー |
| 入社日 | date | 仕入発生日・決済期日計算 |
| 成約日 | date | 売上発生日 |
| 税抜売上 | number/formula | 売上金額 |
| 税抜集客手数料 | number/formula | 仕入金額 |
| 売上決済期日 | date/formula | 売上決済期日 |
| 仕入決済期日 | date/formula | 仕入決済期日 |
| 返金率 | number | 返金処理時 |
| 返金後入金売上 | number | 返金処理時 |
| 返金後集客手数料 | number | 返金処理時 |
| freee売上取引ID | number | 入社前辞退時の削除 |
| freee支出取引ID | number | 入社前辞退時の削除 |
| PCA仕入高 | number | PCA仕入仕訳の金額 |
| PCA仕入決済期日 | date | PCA仕入決済期日 |
| 求職者 | relation | 求職者名取得（別ページAPIコール） |

---

## 残課題・未実装事項

### 1. PCA担当パートナー名の取得（優先度：高）
- **現状**: `partner_name: None`（空欄）
- **対応**: Notionの「担当パートナー」プロパティ（relation or select）から取得する
- **実装場所**: `notion_client.py` に取得関数追加、`rules.py` の `_extract_props` に追加

### 2. 集客経路のrollup取得確認（優先度：中）
- **現状**: `get_rollup_select("集客経路")` で取得を試みているが、Notionの実際のプロパティ名・型が要確認
- **確認方法**: Notionのプロパティ名が「集客経路」で合っているか、rollupかselectかを確認

### 3. freee部門マスタとの照合（優先度：中）
- **現状**: 「本店CA」「本店PCA」という名前でfreeeに送っているが、freeeの部門マスタに同名の部門が存在するか要確認
- **確認方法**: freeeの「設定 → 部門」で部門名を確認

### 4. メモタグ（freeeのタグ機能）（優先度：低）
- **現状**: tag_namesは空配列で送っている
- **対応**: 必要であれば担当CA名等をfreeeのメモタグに設定可能

### 5. Railwayの課金登録（優先度：高・30日以内）
- **現状**: 30日間/$5のトライアル中
- **対応**: https://railway.app/account/billing でクレジットカード登録（月$5程度）

---

## 操作方法

### アプリの使い方
1. https://notion-freee-production.up.railway.app にアクセス
2. **仕訳プレビュー**: 登録前に内容を確認できる
3. **全件処理（本店CA+PCA）**: 全件を一括でfreeeに登録
4. **本店CAのみ / PCAのみ**: 個別に処理
5. 自動ポーリング: 1時間ごとに自動で処理（ダッシュボードで停止可能）

### コードを修正する場合
1. `/home/ubuntu/notion_freee/` のコードを編集
2. `git add -A && git commit -m "修正内容" && git push origin main`
3. Railwayが自動デプロイ（1〜2分）

### freee認証が切れた場合
- https://notion-freee-production.up.railway.app/auth/freee にアクセスして再認証

---

## freeeアプリ設定

- **コールバックURL**: `https://notion-freee-production.up.railway.app/auth/freee/callback`
- **スコープ**: `read write`
- **管理画面**: https://app.secure.freee.co.jp/developers/applications
