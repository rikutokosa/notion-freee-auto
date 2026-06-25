"""
Notion APIで本店CA・PCA両DBに「請求有無（選択）」セレクト型フィールドを追加するスクリプト
- 要請求: 赤（red）
- 請求不要: 青（blue）
"""
import os
import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "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")
NOTION_DB_ID_HONTEN = os.environ.get("NOTION_DB_ID_HONTEN", "320a7a34-dbe2-8082-8055-c57f9b8a04bb")
NOTION_DB_ID_PCA = os.environ.get("NOTION_DB_ID_PCA", "32fa7a34-dbe2-8005-ab91-ff33d64506e0")
NOTION_VERSION = "2022-06-28"

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

payload = {
    "properties": {
        "請求有無（選択）": {
            "select": {
                "options": [
                    {"name": "要請求", "color": "red"},
                    {"name": "請求不要", "color": "blue"},
                ]
            }
        }
    }
}

for db_name, db_id in [("本店CA", NOTION_DB_ID_HONTEN), ("PCA", NOTION_DB_ID_PCA)]:
    url = f"https://api.notion.com/v1/databases/{db_id}"
    resp = requests.patch(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        print(f"✅ {db_name} DB: 「請求有無（選択）」フィールド追加成功")
    else:
        print(f"❌ {db_name} DB: 失敗 status={resp.status_code}")
        print(resp.text[:500])
