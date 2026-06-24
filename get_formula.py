"""
Notionの本店CA・PCA DBの「売上決済期日」フォーミュラを取得する
"""
import requests
import json

NOTION_TOKEN = "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"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
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
    for name, prop in props.items():
        if prop.get("type") == "formula":
            print(f"\n--- フォーミュラプロパティ: {name} ---")
            formula_expr = prop.get("formula", {}).get("expression", "")
            print(formula_expr)
