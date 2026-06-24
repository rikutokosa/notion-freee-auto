"""
Notionの「仕入決済期日」と「freee集客取引先ID」フォーミュラに
マイナビスカウティングを追加するスクリプト
"""
import requests
import json
import sys
sys.path.insert(0, '.')
from notion_client import NOTION_TOKEN, NOTION_DB_ID_HONTEN

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

# ============================================================
# 1. 仕入決済期日フォーミュラ（マイナビスカウティングを追加）
#    マイナビ転職と同じ「入社翌々月10日」ルール
# ============================================================

# 元の式から最後の parseDate("") の前に マイナビスカウティング分岐を挿入
# マイナビ転職と全く同じロジック（dateAdd 2months → 10日に設定）

SHIIRE_KESSAI_FORMULA = (
    'ifs(\n'
    '  contains({{notion:block_property:t`K]:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "入社前辞退") or\n'
    '  contains({{notion:block_property:t`K]:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "退職"), parseDate(""),\n'
    '\n'
    '  empty({{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}) or empty({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}), parseDate(""),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "RDS"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(3, "months"),\n'
    '      d.dateSubtract(d.date(), "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "マイナビ転職"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date() - 10, "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "マイナビスカウティング"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date() - 10, "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "dodaX"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date(), "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "キミナラ"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date(), "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "ワンキャリア"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date(), "days")\n'
    '    ),\n'
    '\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.format(), "openwork"),\n'
    '    let(\n'
    '      d,\n'
    '      {{notion:block_property:uzlJ:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}.dateAdd(2, "months"),\n'
    '      d.dateSubtract(d.date(), "days")\n'
    '    ),\n'
    '\n'
    '  parseDate("")\n'
    ')'
)

# ============================================================
# 2. freee集客取引先ID（マイナビスカウティング → マイナビ転職と同じ 52326121）
# ============================================================

FREEE_PARTNER_ID_FORMULA = (
    '/* 集客経路（マイナビスカウティング追加版） */\n'
    'ifs(\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}, "RDS"), 61974688,\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}, "マイナビ転職"), 52326121,\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}, "マイナビスカウティング"), 52326121,\n'
    '  contains({{notion:block_property:?PCT:00000000-0000-0000-0000-000000000000:475a7a34-dbe2-81d6-8d73-0003bef9e85d}}, "キミナラ"), 112569342,\n'
    '  toNumber("")\n'
    ')'
)

# ============================================================
# 更新実行
# ============================================================
payload = {
    "properties": {
        "仕入決済期日": {
            "formula": {
                "expression": SHIIRE_KESSAI_FORMULA
            }
        },
        "freee集客取引先ID": {
            "formula": {
                "expression": FREEE_PARTNER_ID_FORMULA
            }
        }
    }
}

print("Notionフォーミュラを更新中...")
resp = requests.patch(
    f"https://api.notion.com/v1/databases/{NOTION_DB_ID_HONTEN}",
    headers=headers,
    json=payload
)
print(f"ステータス: {resp.status_code}")
if resp.status_code == 200:
    print("✅ 更新成功")
    # 更新後の式を確認
    db = resp.json()
    for name in ["仕入決済期日", "freee集客取引先ID"]:
        prop = db.get("properties", {}).get(name, {})
        expr = prop.get("formula", {}).get("expression", "")
        print(f"\n=== {name} ===")
        print(expr[:200], "..." if len(expr) > 200 else "")
else:
    print(f"❌ エラー: {resp.text[:500]}")
