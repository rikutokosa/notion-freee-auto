"""
Notionの本店CA・PCA DBの「売上決済期日」フォーミュラにHitolinkを追加する（修正版）
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

    uriage_prop = props.get("売上決済期日")
    if not uriage_prop:
        print("売上決済期日プロパティが見つかりません")
        continue

    current_formula = uriage_prop.get("formula", {}).get("expression", "")

    # 既にHitolinkが含まれているか確認
    if "Hitolink" in current_formula:
        print("既にHitolinkが含まれています。スキップします。")
        continue

    # 末尾の正確なパターンを確認（repr使用）
    tail = current_formula[-300:]
    print("末尾300文字のrepr:")
    print(repr(tail))

    # プロパティIDを特定
    if "sd_%5B" in current_formula:
        job_db_prop_ref = "sd_%5B"
        nyusha_prop_ref = "uzlJ"
        db_block_id = "475a7a34-dbe2-81d6-8d73-0003bef9e85d"
    elif "gA%3BK" in current_formula:
        job_db_prop_ref = "gA%3BK"
        nyusha_prop_ref = "oLjx"
        db_block_id = "475a7a34-dbe2-81d6-8d73-0003bef9e85d"
    else:
        print("プロパティ参照が見つかりません")
        continue

    # 末尾の "parseDate("")\n)" を置換
    # 実際のパターンを確認してから置換
    end_pattern = 'parseDate("")\n)'
    if end_pattern not in current_formula:
        # 別のパターンを試す
        end_pattern = 'parseDate("")\r\n)'
        if end_pattern not in current_formula:
            print(f"末尾パターンが見つかりません")
            continue

    hitolink_addition = f'''contains({{{{notion:block_property:{job_db_prop_ref}:00000000-0000-0000-0000-000000000000:{db_block_id}}}}}.format(), "Hitolink"),
    let(
      d,
      {{{{notion:block_property:{nyusha_prop_ref}:00000000-0000-0000-0000-000000000000:{db_block_id}}}}}.dateAdd(2, "months"),
      d.dateSubtract(d.date(), "days")
    ),
  parseDate("")
)'''

    # 最後の "parseDate("")\n)" を置換（rfindで最後の出現箇所を見つける）
    last_idx = current_formula.rfind(end_pattern)
    if last_idx == -1:
        print("置換位置が見つかりません")
        continue

    new_formula = current_formula[:last_idx] + hitolink_addition

    print(f"\n新フォーミュラ（末尾300文字）:")
    print(new_formula[-300:])

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
        print(f"\n✅ {db_name}の売上決済期日フォーミュラを更新しました")
    else:
        print(f"\n❌ 更新失敗: {update_resp.status_code}")
        print(update_resp.text[:500])
