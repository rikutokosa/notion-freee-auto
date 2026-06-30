# AGENT_RULES.md

このファイルは、notion-freee-auto プロジェクトにおけるAIエージェントの作業規範です。
MEMORY.md は仕様・設計判断の記録、この AGENT_RULES.md は作業手順・禁止事項・完了条件を定めます。

## 1. 最優先原則

このシステムは会計・送金・freee・Notionの本番データを扱う。
便利さより、以下を優先する。

- 壊さない
- 勝手に本番データを変更しない
- シークレットを漏らさない
- 既存の安全機構を無効化しない
- 「やったつもり」で完了報告しない

## 2. 作業開始前のルール

作業開始前に必ず以下を読む。

1. AGENT_RULES.md
2. MEMORY.md

AGENT_RULES.md とユーザーの個別指示が矛盾する場合は、勝手に判断せず確認すること。

## 3. 完了の定義

以下が揃うまで「完了」「対応済み」と報告してはいけない。

1. 該当箇所の git diff
2. scripts/selfcheck.sh の実行結果
3. 修正内容に対応する grep / 確認コマンドの実出力
4. 該当処理の実装箇所・呼び出し箇所・削除箇所のdiff
5. 本番でしか確認できないものは「本番未実施」と明示
6. 実機テストできないものは「未実施」と明示
7. requirements.txt / requirements-dev.txt を変更した場合はそのdiff
8. シークレット検出結果は必ずマスクして提示

grep結果は証拠の一部であって、要件を満たした証明ではない。
grepを通すためだけの表面修正は禁止。

「実装したつもり」「おそらく動く」「確認してください」は完了報告ではない。

## 4. 禁止事項

以下を禁止する。

- 既存の認証、バリデーション、idempotency、dry_run、安全確認を無断で削除・無効化すること
- grepチェックを通すためだけに表面上のimportや文字列を追加し、実際の処理を直さないこと
- py_compile だけで「テスト済み」と報告すること
- 本番のfreee / Notion / OpenAIに、テスト目的で書き込み・削除・送信すること
- シークレットやAPIトークンをログ、diff、報告文に全文表示すること
- PATやAPIトークンをremote URLやコードに埋め込むこと
- 実ファイルを確認せずに「対応済み」と報告すること

## 5. 検証ルール

修正後は必ず scripts/selfcheck.sh を実行する。

selfcheck.sh には最低限以下を含める。

- python3 -m pyflakes 対象ファイル
- python3 -m py_compile 対象ファイル
- git diff --check
- git status --short
- secret検出。ただし値は必ず [REDACTED] にする
- 修正内容に応じたgrep確認

pyflakesの警告が1件でもある場合は不合格。

ただし、AGENT_RULES.md と scripts/selfcheck.sh を初回作成するブートストラップ作業に限り、selfcheck.sh 実行前の状態であるため、cat全文とdiff提示をもって確認する。
以後はselfcheck必須。

## 6. requirementsの扱い

本番コードで新しい外部ライブラリを使う場合は requirements.txt に追加する。

開発・検証専用ツールは requirements-dev.txt に追加する。

例：

- 本番アプリで使う requests / openpyxl / xlrd など → requirements.txt
- pyflakes などの検証ツール → requirements-dev.txt

requirementsを変更した場合は、必ずdiffを提示する。

## 7. セキュリティルール

シークレットを検出した場合、報告には値を絶対に出さない。

悪い例：

NOTION_TOKEN=ntn_xxxxx

良い例：

NOTION_TOKEN=[REDACTED]

GitHub PAT、Notion Token、OpenAI API Key、Railway Token、Slack Webhook、freee client secret はすべて秘匿対象。

## 8. 本番テストの扱い

本番環境での書き込み・削除・送信テストは、ユーザーの明示承認なしに行わない。

本番確認が必要な場合は、以下のように報告する。

- 本番未実施
- 理由：本番freee/Notionに影響するため
- ユーザー承認後に実施可能な確認項目

Basic認証や /health の疎通確認など、データを書き換えない確認のみ、ユーザー承認後に実施する。

## 9. 報告フォーマット

完了報告は必ず以下の形式にする。

1. 変更ファイル一覧
2. 要件ごとの対応内容
   - 要件
   - 該当diff
   - 検証コマンド
   - 検証結果
3. selfcheck.sh 実行結果
4. 未実施・未確認項目
5. 本番反映可否
6. 注意点・次に必要な作業

## 10. ドキュメント更新ルール

業務ルール、運用ルール、環境変数ルール、画面上に表示される説明を変更した場合は、関連するドキュメントや rules_tabs.html も同じコミットで更新する。

単なるバグ修正、import修正、内部実装修正では、不要なドキュメント更新をしない。

## 11. Git / トークン運用

GitHubへのpushでは、PATをremote URLに埋め込まない。

推奨：

- GitHub CLI
- SSHキー
- 期限付き・最小権限のトークンを環境変数経由で使用

Railway環境変数はRailway管理画面または安全なCLIで設定する。
APIトークンやシークレットをファイルにハードコードしない。
