import requests, os, json

# Railwayのデバッグエンドポイント経由でfreee APIを叩く
# まずトークンを取得
resp = requests.get("https://notion-freee-production.up.railway.app/api/debug_receipts")
print("debug_receipts status:", resp.status_code)

# 直接freee APIを叩く（トークンはサーバー側にある）
# app.pyにデバッグエンドポイントを追加して取引先一覧を取得する
