"""
Notionフィールド再設計スクリプト

変更内容:
1. 本店CA DB・PCA DB 両方:
   - 「freee売上取引ID」(formula) → 「freee売上取引先ID」(formula) に名前変更
   - 「freee支出取引ID」(formula) → 「freee集客取引先ID」(formula) に名前変更
   - 「freee売上取引ID」(number) を新規追加 → freeeの仕訳ID保存用
   - 「freee仕入取引ID」(number) を新規追加 → freeeの仕入仕訳ID保存用
   - 「freee仕入取引ID（PCA）」(number) を新規追加（PCA DBのみ）

注意: Notion APIではformulaフィールドの名前変更はPATCH /databases/{id} で可能
"""
import requests
import json

NOTION_TOKEN = "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"
NOTION_DB_ID_HONTEN = "320a7a34-dbe2-8082-8055-c57f9b8a04bb"
NOTION_DB_ID_PCA = "32fa7a34-dbe2-8005-ab91-ff33d64506e0"

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def get_db_props(db_id: str) -> dict:
    resp = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=headers)
    resp.raise_for_status()
    return resp.json().get("properties", {})


def update_db(db_id: str, properties: dict) -> dict:
    payload = {"properties": properties}
    resp = requests.patch(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=headers,
        json=payload,
    )
    if resp.status_code != 200:
        print(f"ERROR: {resp.status_code} {resp.text[:500]}")
        resp.raise_for_status()
    return resp.json()


def setup_honten():
    print("=== 本店CA DB のフィールド設定 ===")
    props = get_db_props(NOTION_DB_ID_HONTEN)

    changes = {}

    # 1. 「freee売上取引ID」(formula) → 「freee売上取引先ID」に名前変更
    if "freee売上取引ID" in props and props["freee売上取引ID"]["type"] == "formula":
        print("  「freee売上取引ID」(formula) → 「freee売上取引先ID」に名前変更")
        changes["freee売上取引ID"] = {"name": "freee売上取引先ID"}

    # 2. 「freee支出取引ID」(formula) → 「freee集客取引先ID」に名前変更
    if "freee支出取引ID" in props and props["freee支出取引ID"]["type"] == "formula":
        print("  「freee支出取引ID」(formula) → 「freee集客取引先ID」に名前変更")
        changes["freee支出取引ID"] = {"name": "freee集客取引先ID"}

    # 3. 「freee売上取引ID」(number) を新規追加
    if "freee売上取引ID" not in props or props["freee売上取引ID"]["type"] == "formula":
        print("  「freee売上取引ID」(number) を新規追加")
        changes["freee売上取引ID"] = {"name": "freee売上取引先ID"}  # 上記と同じキーなので先に名前変更してから追加

    if changes:
        result = update_db(NOTION_DB_ID_HONTEN, changes)
        print(f"  名前変更完了")

    # 名前変更後に新規フィールドを追加
    props_after = get_db_props(NOTION_DB_ID_HONTEN)
    new_fields = {}

    if "freee売上取引ID" not in props_after:
        print("  「freee売上取引ID」(number) を新規追加")
        new_fields["freee売上取引ID"] = {"number": {"format": "number"}}

    if "freee仕入取引ID" not in props_after:
        print("  「freee仕入取引ID」(number) を新規追加")
        new_fields["freee仕入取引ID"] = {"number": {"format": "number"}}

    if new_fields:
        result = update_db(NOTION_DB_ID_HONTEN, new_fields)
        print(f"  新規フィールド追加完了")

    print("  本店CA DB 完了\n")


def setup_pca():
    print("=== PCA DB のフィールド設定 ===")
    props = get_db_props(NOTION_DB_ID_PCA)

    changes = {}

    # 1. 「freee売上取引ID」(formula) → 「freee売上取引先ID」に名前変更
    if "freee売上取引ID" in props and props["freee売上取引ID"]["type"] == "formula":
        print("  「freee売上取引ID」(formula) → 「freee売上取引先ID」に名前変更")
        changes["freee売上取引ID"] = {"name": "freee売上取引先ID"}

    # 2. 「freee支出取引ID」(formula) → 「freee集客取引先ID」に名前変更
    if "freee支出取引ID" in props and props["freee支出取引ID"]["type"] == "formula":
        print("  「freee支出取引ID」(formula) → 「freee集客取引先ID」に名前変更")
        changes["freee支出取引ID"] = {"name": "freee集客取引先ID"}

    if changes:
        result = update_db(NOTION_DB_ID_PCA, changes)
        print(f"  名前変更完了")

    # 名前変更後に新規フィールドを追加
    props_after = get_db_props(NOTION_DB_ID_PCA)
    new_fields = {}

    if "freee売上取引ID" not in props_after:
        print("  「freee売上取引ID」(number) を新規追加")
        new_fields["freee売上取引ID"] = {"number": {"format": "number"}}

    if "freee仕入取引ID" not in props_after:
        print("  「freee仕入取引ID」(number) を新規追加")
        new_fields["freee仕入取引ID"] = {"number": {"format": "number"}}

    if "freee仕入取引ID（PCA）" not in props_after:
        print("  「freee仕入取引ID（PCA）」(number) を新規追加")
        new_fields["freee仕入取引ID（PCA）"] = {"number": {"format": "number"}}

    if new_fields:
        result = update_db(NOTION_DB_ID_PCA, new_fields)
        print(f"  新規フィールド追加完了")

    print("  PCA DB 完了\n")


def verify():
    print("=== 変更後の確認 ===")
    for db_name, db_id in [("本店CA", NOTION_DB_ID_HONTEN), ("PCA", NOTION_DB_ID_PCA)]:
        props = get_db_props(db_id)
        print(f"\n{db_name} DB:")
        for name, info in sorted(props.items()):
            if "freee" in name or "取引" in name:
                ptype = info.get("type", "?")
                print(f"  [{ptype}] {name}")


if __name__ == "__main__":
    setup_honten()
    setup_pca()
    verify()
    print("\n完了!")
