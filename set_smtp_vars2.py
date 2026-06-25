import requests

RAILWAY_TOKEN = "1b2263ba-2677-40c9-b94a-6f93af1ca488"
PROJECT_ID = "bd98ef72-9f60-47cc-bb57-83aa40281857"
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
    print(f"SUCCESS: {data}")
    print("\n設定した環境変数:")
    for k, v in env_vars.items():
        print(f"  {k} = {v}")
