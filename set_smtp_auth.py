import requests

RAILWAY_TOKEN = "1b2263ba-2677-40c9-b94a-6f93af1ca488"
PROJECT_ID = "bd98ef72-9f60-47cc-bb57-83aa40281857"
SERVICE_ID = "db0ffb4a-777f-4ba3-9064-63220f0fa541"
ENV_ID = "adbae2d0-6c38-4e23-8721-9796319cf631"

headers = {
    "Authorization": f"Bearer {RAILWAY_TOKEN}",
    "Content-Type": "application/json",
}

# アプリパスワードのスペースを除去
env_vars = {
    "SMTP_USER": "bearsnavi.sidesales@gmail.com",
    "SMTP_PASS": "fbijsetoelii ndjb".replace(" ", ""),
}

query = """
mutation variableCollectionUpsert($input: VariableCollectionUpsertInput!) {
    variableCollectionUpsert(input: $input)
}
"""

variables = {
    "input": {
        "projectId": PROJECT_ID,
        "serviceId": SERVICE_ID,
        "environmentId": ENV_ID,
        "variables": env_vars,
    }
}

resp = requests.post(
    "https://backboard.railway.com/graphql/v2",
    headers=headers,
    json={"query": query, "variables": variables},
)
data = resp.json()
if "errors" in data:
    print(f"ERROR: {data['errors']}")
else:
    print("SUCCESS")
    print("SMTP_USER:", env_vars["SMTP_USER"])
    print("SMTP_PASS: (設定済み)")
