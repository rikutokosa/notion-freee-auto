import requests
import json

RAILWAY_TOKEN = "1b2263ba-2677-40c9-b94a-6f93af1ca488"
PROJECT_ID = "bd98ef72-9f60-47cc-bb57-83aa40281857"
SERVICE_ID = "db0ffb4a-777f-4ba3-9064-63220f0fa541"
ENV_ID = "adbae2d0-6c38-4e23-8721-9796319cf631"

headers = {
    "Authorization": f"Bearer {RAILWAY_TOKEN}",
    "Content-Type": "application/json",
}

env_vars = {
    "NOTION_TOKEN": "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",
    "NOTION_DB_ID_HONTEN": "320a7a34-dbe2-8082-8055-c57f9b8a04bb",
    "NOTION_DB_ID_PCA": "32fa7a34-dbe2-8005-ab91-ff33d64506e0",
    "FREEE_CLIENT_ID": "740864584696172",
    "FREEE_CLIENT_SECRET": "IIj_jZ1tTpacblsP9hmBAvWfx7f84bfA6OlDTE57eaecLldYTWjCIUs8rb7X627F9nKE4To5C5ByvJgWehTytg",
    "FREEE_COMPANY_ID": "1856949",
    "FLASK_SECRET_KEY": "notion-freee-secret-key-2024-bears-navi",
    "PORT": "8080",
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
