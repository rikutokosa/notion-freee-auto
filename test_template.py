"""
Flaskのテストクライアントを使わず、テンプレートだけ直接テストする。
バックグラウンドスレッドを起動しないようにする。
"""
import os
os.environ['FREEE_AUTO_STOPPED'] = '1'  # ポーリングを無効化

import sys
sys.path.insert(0, '/home/ubuntu/notion-freee-auto')

# appをインポートする前にポーリングを止める
import threading
_orig_start = threading.Thread.start
def _patched_start(self, *args, **kwargs):
    # daemonスレッドの自動起動を抑制
    pass
threading.Thread.start = _patched_start

from flask import render_template_string
import app as app_module

# テストデータをassistant_logに追加
app_module.assistant_log.append({
    'source': 'assistant',
    'freee_id': 12345,
    'issue_date': '2026-06-19',
    'partner_name': 'テスト株式会社',
    'deal_type': 'income',
    'amount': 100000,
    'registered_at': '2026-06-19 10:30',
    'note': 'マイナビへの広告費',
})

flask_app = app_module.app

with flask_app.test_request_context('/preview'):
    from flask import render_template
    html = render_template('preview.html', previews=[], db_type='all', assistant_log=app_module.assistant_log[:50])

print(f'HTML length: {len(html)}')

checks = [
    ('アシスタント登録セクション', 'アシスタント登録' in html),
    ('テスト株式会社が表示', 'テスト株式会社' in html),
    ('Notion取込バッジ', 'Notion取込' in html),
    ('freeeリンク', 'secure.freee.co.jp' in html),
    ('チャット指示が削除', 'チャット指示' not in html),
    ('仕訳アシスタントがナビにある', '仕訳アシスタント' in html),
    ('仕訳プレビューがナビにある', '仕訳プレビュー' in html),
]

all_ok = True
for name, ok in checks:
    status = '✅' if ok else '❌'
    print(f'{status} {name}')
    if not ok:
        all_ok = False

print()
print('全テスト通過！' if all_ok else '一部テスト失敗')
