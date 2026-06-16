import requests, json

token = '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'
db_id = '320a7a34-dbe2-8082-8055-c57f9b8a04bb'

headers = {
    'Authorization': f'Bearer {token}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}

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
print(f'取得件数: {len(results)}')
if results:
    props = results[0]['properties']
    print(f'全プロパティキー: {list(props.keys())}')
    for key in ['担当CA', '集客経路', '集客取引先ID', '売上取引先ID', '求職者']:
        if key in props:
            print(f'\n=== {key} ===')
            print(json.dumps(props[key], ensure_ascii=False, indent=2))
        else:
            print(f'\n{key}: プロパティなし')
