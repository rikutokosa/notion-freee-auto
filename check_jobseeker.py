import requests, json

token = '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'
db_id = '320a7a34-dbe2-8082-8055-c57f9b8a04bb'

headers = {
    'Authorization': f'Bearer {token}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}

# 本部確認済レコードを1件取得
payload = {
    'filter': {
        'property': '請求ステータス',
        'select': {'equals': '本部確認済'}
    },
    'page_size': 1
}

resp = requests.post(f'https://api.notion.com/v1/databases/{db_id}/query', headers=headers, json=payload)
data = resp.json()
results = data.get('results', [])

if results:
    props = results[0]['properties']
    
    # 求職者relationのIDを取得
    jobseeker_rel = props.get('求職者', {}).get('relation', [])
    print(f'求職者relation: {jobseeker_rel}')
    
    if jobseeker_rel:
        page_id = jobseeker_rel[0]['id']
        print(f'\n求職者ページID: {page_id}')
        
        # 求職者ページを取得
        page_resp = requests.get(f'https://api.notion.com/v1/pages/{page_id}', headers=headers)
        page_data = page_resp.json()
        
        if page_data.get('object') == 'page':
            page_props = page_data.get('properties', {})
            print(f'求職者DBプロパティキー: {list(page_props.keys())}')
            
            # タイトル（名前）を取得
            for key, val in page_props.items():
                if val.get('type') == 'title':
                    texts = val.get('title', [])
                    name = texts[0].get('plain_text', '') if texts else ''
                    print(f'\n求職者名 ({key}): {name}')
        else:
            print(f'エラー: {json.dumps(page_data, ensure_ascii=False, indent=2)}')
