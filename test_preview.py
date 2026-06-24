import sys
sys.path.insert(0, '/home/ubuntu/notion-freee-auto')

from app import app, assistant_log

# assistant_logにテストデータを追加
assistant_log.append({
    'source': 'assistant',
    'freee_id': 12345,
    'issue_date': '2026-06-19',
    'partner_name': 'テスト株式会社',
    'deal_type': 'income',
    'amount': 100000,
    'registered_at': '2026-06-19 10:30',
    'note': 'マイナビへの広告費を登録して',
})

with app.test_client() as c:
    resp = c.get('/preview')
    body = resp.data.decode('utf-8')
    print(f'HTTP status: {resp.status_code}')
    
    checks = [
        ('アシスタント登録セクション', 'アシスタント登録' in body),
        ('チャット指示が削除されている', 'チャット指示' not in body),
        ('仕訳アシスタントがナビにある', '仕訳アシスタント' in body),
        ('仕訳プレビューがナビにある', '仕訳プレビュー' in body),
        ('Notion取込バッジ', 'Notion取込' in body),
        ('テスト株式会社が表示される', 'テスト株式会社' in body),
    ]
    
    all_ok = True
    for name, result in checks:
        status = '✅' if result else '❌'
        print(f'{status} {name}')
        if not result:
            all_ok = False
    
    # AIエンドポイントのlist.get()エラー修正確認
    import json
    resp2 = c.post('/api/assistant/ai', 
        data=json.dumps({'message': 'テスト', 'history': [], 'master': {}}),
        content_type='application/json')
    body2 = resp2.data.decode('utf-8')
    if 'OpenAI APIキーが設定されていません' in body2 or resp2.status_code in (200, 500):
        # 500でもlist.get()エラーではないことを確認
        data2 = json.loads(body2)
        if "'list' object has no attribute 'get'" in str(data2):
            print("❌ list.get()エラーがまだ残っている")
            all_ok = False
        else:
            print(f"✅ AIエンドポイント: list.get()エラーなし (status={resp2.status_code})")
    
    print()
    print('全テスト通過' if all_ok else '一部テスト失敗')
