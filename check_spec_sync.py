#!/usr/bin/env python3
"""
SYSTEM_SPEC.md / rules_tabs.html とコードの整合性を自動検証するスクリプト。

GitHub Actions（CI）で自動実行され、以下を検証する:
  1. SYSTEM_SPEC.md に記載された集客経路ルール一覧が rules.py の RULES 辞書と一致するか
  2. SYSTEM_SPEC.md に記載された定数値がコード内の実際の値と一致するか
  3. SYSTEM_SPEC.md に記載されたPENDING_STATUSESが notion_client.py と一致するか
  4. SYSTEM_SPEC.md に記載された画面・機能仕様キーワードがコードに実装されているか

不整合があった場合は exit code 1 で終了し、GitHub Actions で警告が出る。
"""
import sys
import re
import os
import importlib.util

# ============================================================
# ユーティリティ
# ============================================================

def load_module_from_file(module_name: str, file_path: str):
    """ファイルパスからモジュールを動的にロードする"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    # 依存モジュールのimportエラーを無視するためにモックする
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, ModuleNotFoundError):
        # 外部依存（freee_client等）がない環境でも定数だけ読めればOK
        pass
    return module


def read_file(path: str) -> str:
    """ファイルを読み込む"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# チェック関数
# ============================================================

def check_rules_keys(spec_content: str, rules_dict: dict) -> list[str]:
    """
    SYSTEM_SPEC.md の集客経路ルール表に記載されたキーが
    rules.py の RULES 辞書のキーと一致するか検証する。
    """
    errors = []

    # SYSTEM_SPEC.md のルール表からキー名を抽出
    # 形式: | 1 | Circus | circus株式会社 | ...
    spec_keys = set()
    pattern = r"\|\s*\d+\s*\|\s*(.+?)\s*\|"
    in_rules_section = False
    for line in spec_content.split("\n"):
        if "集客経路ルール" in line and "rules.py" in line:
            in_rules_section = True
            continue
        if in_rules_section and line.startswith("## "):
            break
        if in_rules_section and line.startswith("|"):
            m = re.match(pattern, line)
            if m:
                key = m.group(1).strip()
                if key and key != "No" and key != "キー" and not key.startswith("-"):
                    spec_keys.add(key)

    code_keys = set(rules_dict.keys())

    # SYSTEM_SPEC.md にあるがコードにないキー
    spec_only = spec_keys - code_keys
    if spec_only:
        errors.append(
            f"SYSTEM_SPEC.md に記載されているがコードに存在しないルールキー: {spec_only}"
        )

    # コードにあるがSYSTEM_SPEC.md にないキー
    code_only = code_keys - spec_keys
    if code_only:
        errors.append(
            f"コードに存在するがSYSTEM_SPEC.md に記載されていないルールキー: {code_only}"
        )

    return errors


def check_constants(spec_content: str, base_dir: str) -> list[str]:
    """
    SYSTEM_SPEC.md に記載された定数値がコード内の実際の値と一致するか検証する。
    """
    errors = []

    # --- DBパス ---
    # SYSTEM_SPEC.md: `/data/chat_history.db` が記載されているはず
    app_content = read_file(os.path.join(base_dir, "app.py"))
    db_path_match = re.search(r'_DB_PATH\s*=.*?"(.+?)"', app_content)
    if db_path_match:
        # パスの末尾部分（ファイル名）を取得
        actual_db_filename = os.path.basename(db_path_match.group(1))
    else:
        # os.path.join形式の場合
        db_path_match = re.search(r'_DB_PATH\s*=\s*os\.path\.join\(.+?,\s*"(.+?)"\)', app_content)
        actual_db_filename = db_path_match.group(1) if db_path_match else None

    if actual_db_filename and actual_db_filename not in spec_content:
        errors.append(
            f"DBファイル名の不一致: コード={actual_db_filename}, SYSTEM_SPEC.md に記載なし"
        )

    # --- FBファイル依頼人コード ---
    payment_path = os.path.join(base_dir, "payment.py")
    if os.path.exists(payment_path):
        payment_content = read_file(payment_path)
        code_match = re.search(r'REQUESTER_CODE\s*=\s*"(.+?)"', payment_content)
        name_match = re.search(r'REQUESTER_NAME_KANA\s*=\s*"(.+?)"', payment_content)

        if code_match and code_match.group(1) not in spec_content:
            errors.append(
                f"FB依頼人コードの不一致: コード={code_match.group(1)}, SYSTEM_SPEC.md に記載なし"
            )
        if name_match and name_match.group(1) not in spec_content:
            errors.append(
                f"FB依頼人名の不一致: コード={name_match.group(1)}, SYSTEM_SPEC.md に記載なし"
            )

    # --- 振込依頼済タグID ---
    freee_path = os.path.join(base_dir, "freee_client.py")
    if os.path.exists(freee_path):
        freee_content = read_file(freee_path)
        tag_match = re.search(r'FURIKOMI_TAG_ID\s*=\s*(\d+)', freee_content)
        if tag_match and tag_match.group(1) not in spec_content:
            errors.append(
                f"振込依頼済タグIDの不一致: コード={tag_match.group(1)}, SYSTEM_SPEC.md に記載なし"
            )

    # --- CSS部門ID ---
    if os.path.exists(freee_path):
        freee_content = read_file(freee_path)
        css_match = re.search(r'CSS_SECTION_IDS\s*=\s*\{(.+?)\}', freee_content)
        if css_match:
            code_ids = set(re.findall(r'\d+', css_match.group(1)))
            for cid in code_ids:
                if cid not in spec_content:
                    errors.append(
                        f"CSS部門ID {cid} がSYSTEM_SPEC.md に記載されていない"
                    )

    return errors


def check_pending_statuses(spec_content: str, base_dir: str) -> list[str]:
    """
    SYSTEM_SPEC.md に記載されたPENDING_STATUSESが
    notion_client.py の実際の定義と一致するか検証する。
    """
    errors = []

    notion_path = os.path.join(base_dir, "notion_client.py")
    if not os.path.exists(notion_path):
        return errors

    notion_content = read_file(notion_path)

    # コードからPENDING_STATUSES_HONTENを抽出
    honten_match = re.search(
        r'PENDING_STATUSES_HONTEN\s*=\s*\[([^\]]+)\]', notion_content
    )
    if honten_match:
        honten_statuses = re.findall(r'"(.+?)"', honten_match.group(1))
        for status in honten_statuses:
            if status not in spec_content:
                errors.append(
                    f"本店ステータス「{status}」がSYSTEM_SPEC.md に記載されていない"
                )

    # コードからPENDING_STATUSES_PCAを抽出
    pca_match = re.search(
        r'PENDING_STATUSES_PCA\s*=\s*\[([^\]]+)\]', notion_content
    )
    if pca_match:
        pca_statuses = re.findall(r'"(.+?)"', pca_match.group(1))
        for status in pca_statuses:
            if status not in spec_content:
                errors.append(
                    f"PCAステータス「{status}」がSYSTEM_SPEC.md に記載されていない"
                )

    return errors


def check_ui_spec_keywords(spec_content: str, base_dir: str) -> list[str]:
    """
    SYSTEM_SPEC.md の「12-7. CIチェック対象の機能仕様キーワード」に記載されたキーワードが
    実際のコードファイルに存在するか検証する。

    キーワードテーブルの形式:
      | キーワード | 検証対象ファイル | 意味 |
      | `alertHtml` | `templates/index.html` | ... |
    """
    errors = []

    # SYSTEM_SPEC.md からキーワードテーブルを抽出
    # セクション「12-7. CIチェック対象の機能仕様キーワード」を探す
    in_section = False
    for line in spec_content.split("\n"):
        if "12-7" in line and "CIチェック対象" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.startswith("|"):
            # ヘッダー行・区切り行をスキップ
            if "キーワード" in line or "---" in line:
                continue
            # | `keyword` | `filename` | 意味 | の形式をパース
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) < 2:
                continue
            # バッククォートを除去
            keyword_raw = parts[0].strip("`")
            file_raw = parts[1].strip("`")

            target_path = os.path.join(base_dir, file_raw)
            if not os.path.exists(target_path):
                errors.append(
                    f"機能仕様チェック: 検証対象ファイル '{file_raw}' が存在しません"
                )
                continue

            file_content = read_file(target_path)
            # キーワードは正規表現パターンとして扱う
            try:
                if not re.search(keyword_raw, file_content):
                    meaning = parts[2] if len(parts) > 2 else ""
                    errors.append(
                        f"機能仕様未実装: '{keyword_raw}' が {file_raw} に存在しません（{meaning}）"
                    )
            except re.error:
                # 正規表現エラーの場合は単純な文字列検索にフォールバック
                if keyword_raw not in file_content:
                    meaning = parts[2] if len(parts) > 2 else ""
                    errors.append(
                        f"機能仕様未実装: '{keyword_raw}' が {file_raw} に存在しません（{meaning}）"
                    )

    return errors


def check_rules_in_rulebook(base_dir: str, rules_dict: dict) -> list[str]:
    """
    rules_tabs.html に集客経路ルール表が動的に生成される構造があるか検証する。
    Jinja2テンプレートの場合、キーが動的に展開されるため、
    テンプレート内に `rules.items()` のループが存在することを確認する。
    静的HTMLの場合は各キーがハードコードされているか確認する。
    """
    errors = []
    rulebook_path = os.path.join(base_dir, "templates", "rules_tabs.html")
    if not os.path.exists(rulebook_path):
        errors.append("templates/rules_tabs.html が存在しません")
        return errors

    rulebook_content = read_file(rulebook_path)

    # Jinja2テンプレートで動的にルールを展開している場合はOK
    if "rules.items()" in rulebook_content or "rules_dict.items()" in rulebook_content:
        # テンプレートがrules.pyのRULES辞書を動的に展開しているので、
        # コードとの乖離は発生しない（自動同期）
        return errors

    # 静的HTMLの場合: 各キーがハードコードされているか確認
    for key in rules_dict.keys():
        if key not in rulebook_content:
            errors.append(
                f"ルールキー「{key}」がrules_tabs.html に記載されていない"
            )

    return errors


# ============================================================
# メイン
# ============================================================

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    spec_path = os.path.join(base_dir, "SYSTEM_SPEC.md")

    if not os.path.exists(spec_path):
        print("❌ SYSTEM_SPEC.md が見つかりません")
        sys.exit(1)

    spec_content = read_file(spec_path)

    # rules.pyのRULES辞書を読み込む（外部依存なしで）
    rules_path = os.path.join(base_dir, "rules.py")
    rules_content = read_file(rules_path)
    # RULES辞書のキーを正規表現で抽出（importせずに静的解析）
    rules_keys = re.findall(r'^\s+"(.+?)":\s*\{', rules_content, re.MULTILINE)
    rules_dict = {k: {} for k in rules_keys}

    all_errors = []

    print("=" * 60)
    print("SYSTEM_SPEC.md 整合性チェック")
    print("=" * 60)

    # 1. ルールキーの整合性
    print("[1/5] 集客経路ルールキーの検証...")
    errs = check_rules_keys(spec_content, rules_dict)
    all_errors.extend(errs)
    print(f"  → {'✅ OK' if not errs else '❌ ' + str(len(errs)) + '件の不整合'}")

    # 2. 定数値の整合性
    print("\n[2/5] 定数値の検証...")
    errs = check_constants(spec_content, base_dir)
    all_errors.extend(errs)
    print(f"  → {'✅ OK' if not errs else '❌ ' + str(len(errs)) + '件の不整合'}")

    # 3. PENDING_STATUSESの整合性
    print("\n[3/5] PENDING_STATUSESの検証...")
    errs = check_pending_statuses(spec_content, base_dir)
    all_errors.extend(errs)
    print(f"  → {'✅ OK' if not errs else '❌ ' + str(len(errs)) + '件の不整合'}")

    # 4. ルールブック（rules_tabs.html）の整合性
    print("\n[4/5] rules_tabs.html の検証...")
    errs = check_rules_in_rulebook(base_dir, rules_dict)
    all_errors.extend(errs)
    print(f"  → {'✅ OK' if not errs else '❌ ' + str(len(errs)) + '件の不整合'}")

    # 5. 画面・機能仕様キーワードの整合性
    print("\n[5/5] 画面・機能仕様キーワードの検証...")
    errs = check_ui_spec_keywords(spec_content, base_dir)
    all_errors.extend(errs)
    print(f"  → {'✅ OK' if not errs else '❌ ' + str(len(errs)) + '件の不整合'}")

    # 結果サマリー
    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ {len(all_errors)}件の不整合が検出されました:\n")
        for i, err in enumerate(all_errors, 1):
            print(f"  {i}. {err}")
        print("\n⚠️  SYSTEM_SPEC.md またはコードを修正してください。")
        sys.exit(1)
    else:
        print("✅ すべてのチェックに合格しました。コードと仕様書は整合しています。")
        sys.exit(0)


if __name__ == "__main__":
    main()
