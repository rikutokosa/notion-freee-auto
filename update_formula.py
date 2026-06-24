"""
Notionの本店CA・PCA DBの「売上決済期日」フォーミュラにHitolinkを追加する
"""
import requests
import json

NOTION_TOKEN = "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"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

DB_IDS = {
    "本店CA": "320a7a34-dbe2-8082-8055-c57f9b8a04bb",
    "PCA": "32fa7a34-dbe2-8005-ab91-ff33d64506e0",
}

# まず現在のフォーミュラを取得して、プロパティIDを確認する
for db_name, db_id in DB_IDS.items():
    print(f"\n{'='*60}")
    print(f"【{db_name}】DB ID: {db_id}")
    print('='*60)
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=HEADERS
    )
    if resp.status_code != 200:
        print(f"ERROR: {resp.status_code} {resp.text[:200]}")
        continue
    data = resp.json()
    props = data.get("properties", {})

    # 売上決済期日フォーミュラを取得
    uriage_prop = props.get("売上決済期日")
    if not uriage_prop:
        print("売上決済期日プロパティが見つかりません")
        print("利用可能なプロパティ:", list(props.keys())[:20])
        continue

    current_formula = uriage_prop.get("formula", {}).get("expression", "")
    print(f"現在のフォーミュラ（末尾100文字）: ...{current_formula[-100:]}")

    # Hitolinkの条件を追加（Beeの直後、parseDate("")の前に挿入）
    # 「入社翌月末」= 入社日 + 2ヶ月 → その月の末日
    # Beeと同じロジック: dateAdd(2, "months") → dateSubtract(date(), "days")

    # 求人DBプロパティのIDを取得（本店CAとPCAで異なる）
    # 本店CAは "sd_%5B" (求人データベース), PCAは "gA%3BK" (求人データベース)
    # フォーミュラ内のプロパティ参照を確認
    if "sd_%5B" in current_formula:
        # 本店CA: 求人データベースのプロパティID
        job_db_prop_ref = "sd_%5B"
        nyusha_prop_ref = "uzlJ"
    elif "gA%3BK" in current_formula:
        # PCA: 求人データベースのプロパティID
        job_db_prop_ref = "gA%3BK"
        nyusha_prop_ref = "oLjx"
    else:
        print("プロパティ参照が見つかりません")
        continue

    # Beeの条件ブロックを探して、その後にHitolinkを追加
    # 既にHitolinkが含まれているか確認
    if "Hitolink" in current_formula:
        print("既にHitolinkが含まれています。スキップします。")
        continue

    # Beeの条件の後（parseDate("")の前）にHitolinkを挿入
    # パターン: "BEE"),\n    let(\n      ...\n    ),\n  parseDate("")\n)"
    bee_end_marker = '    ),\n  parseDate("")\n)'

    hitolink_block = f'''    ),
  contains({{{{notion:block_property:{job_db_prop_ref}:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}}}.format(), "Hitolink"),
    let(
      d,
      {{{{notion:block_property:{nyusha_prop_ref}:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}}}.dateAdd(2, "months"),
      d.dateSubtract(d.date(), "days")
    ),
  parseDate("")
)'''

    # Beeブロックの末尾を置換
    new_formula = current_formula.replace(bee_end_marker, hitolink_block)

    if new_formula == current_formula:
        print("置換に失敗しました。末尾パターンを確認してください。")
        print(f"末尾200文字: {current_formula[-200:]}")
        continue

    print(f"新フォーミュラ（末尾200文字）: ...{new_formula[-200:]}")

    # Notionのフォーミュラを更新
    payload = {
        "properties": {
            "売上決済期日": {
                "formula": {
                    "expression": new_formula
                }
            }
        }
    }

    update_resp = requests.patch(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=HEADERS,
        json=payload
    )

    if update_resp.status_code == 200:
        print(f"✅ {db_name}の売上決済期日フォーミュラを更新しました")
    else:
        print(f"❌ 更新失敗: {update_resp.status_code}")
        print(update_resp.text[:500])
