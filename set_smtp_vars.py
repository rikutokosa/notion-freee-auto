import requests

RAILWAY_TOKEN = "1b2263ba-2677-40c9-b94a-6f93af1ca488"
SERVICE_ID = "db0ffb4a-777f-4ba3-9064-63220f0fa541"
ENV_ID = "adbae2d0-6c38-4e23-8721-9796319cf631"

headers = {
    "Authorization": f"Bearer {RAILWAY_TOKEN}",
    "Content-Type": "application/json",
}

env_vars = {
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "NOTIFY_TO": "r.kosa@bearsnavi.com",
}

for name, value in env_vars.items():
    query = """
    mutation variableUpsert($input: VariableUpsertInput!) {
        variableUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "serviceId": SERVICE_ID,
            "environmentId": ENV_ID,
            "name": name,
            "value": value,
        }
    }
    resp = requests.post(
        "https://backboard.railway.com/graphql/v2",
        headers=headers,
        json={"query": query, "variables": variables},
    )
    data = resp.json()
    if "errors" in data:
        print(f"ERROR {name}: {data['errors']}")
    else:
        print(f"OK: {name} = {value}")

print("\nSMTP環境変数の設定完了！")
print("\n※ SMTP_USER と SMTP_PASS は送信元Gmailアドレスとアプリパスワードを別途設定してください。")
