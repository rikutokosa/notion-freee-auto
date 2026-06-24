"""
freee API直接テスト
- 仕訳検索（Stellify, 2026-07-01以降）
- 請求書検索（Stellify, 2026-07-01以降）
- 削除APIが正しく動作するか確認（実際には削除しない）
"""
import os
import sys
import json
import requests

# 本番環境のAPIを直接叩いてテスト
BASE_URL = "https://notion-freee-production.up.railway.app"

def test_search_deals():
    print("=== 仕訳検索テスト ===")
    resp = requests.post(f"{BASE_URL}/api/assistant/ai", json={
        "messages": [{"role": "user", "content": "株式会社Stellifyの2026年7月以降の仕訳を検索して"}],
    }, timeout=60)
    print(f"status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"response: {data.get('message', '')[:300]}")
        print(f"tool_calls: {data.get('tool_calls', [])}")
    else:
        print(f"error: {resp.text[:300]}")

def test_search_invoices():
    print("\n=== 請求書検索テスト ===")
    resp = requests.post(f"{BASE_URL}/api/assistant/ai", json={
        "messages": [{"role": "user", "content": "株式会社Stellifyの2026年7月以降の請求書を検索して"}],
    }, timeout=60)
    print(f"status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"response: {data.get('message', '')[:300]}")
        print(f"tool_calls: {data.get('tool_calls', [])}")
    else:
        print(f"error: {resp.text[:300]}")

def test_direct_search_invoices():
    print("\n=== 請求書API直接テスト ===")
    resp = requests.get(f"{BASE_URL}/api/debug_invoices", params={
        "partner_id": 110745827,
        "start_date": "2026-07-01"
    }, timeout=30)
    print(f"status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"stellify_count: {data.get('stellify_count')}")
        for inv in data.get('stellify_invoices', [])[:5]:
            print(f"  id={inv.get('id')} billing_date={inv.get('billing_date')} issue_date={inv.get('issue_date')} amount={inv.get('total_amount')}")
    else:
        print(f"error: {resp.text[:300]}")

def test_direct_search_deals():
    print("\n=== 仕訳API直接テスト ===")
    resp = requests.get(f"{BASE_URL}/api/debug_partners", timeout=30)
    if resp.ok:
        data = resp.json()
        stellify = [p for p in data.get('partners', []) if 'Stellify' in p.get('name', '') or 'ステリファイ' in p.get('name', '')]
        print(f"Stellify取引先: {[(p['id'], p['name']) for p in stellify]}")
    
    # freee APIで直接仕訳検索
    # アクセストークンが必要なのでdebugエンドポイント経由
    resp2 = requests.get(f"{BASE_URL}/api/debug_deals", params={
        "partner_id": 110745827,
        "start_date": "2026-07-01"
    }, timeout=30)
    print(f"deals status: {resp2.status_code}")
    if resp2.ok:
        data2 = resp2.json()
        print(f"deals: {json.dumps(data2, ensure_ascii=False)[:500]}")
    else:
        print(f"deals error: {resp2.text[:200]}")

if __name__ == "__main__":
    test_direct_search_invoices()
    test_direct_search_deals()
