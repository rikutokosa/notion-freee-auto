"""
index.htmlの各タブのinline-rule-sectionをプレースホルダーに置き換えるスクリプト。
rules.htmlをsingle source of truthとし、index.htmlはAPIで動的に読み込む方式にする。
"""
from bs4 import BeautifulSoup
import re

with open("/home/ubuntu/notion-freee-auto/templates/index.html", "r", encoding="utf-8") as f:
    content = f.read()

soup = BeautifulSoup(content, "html.parser")

# タブ名とrules.htmlのtab_idのマッピング
TAB_MAP = {
    "jidou": "tab-jidou",
    "assistant": "tab-assistant",
    "shocho": "tab-shocho",
    "shiharai": "tab-shiharai",
}

# 各タブのinline-rule-sectionを探してプレースホルダーに置き換える
# inline-rule-sectionはidが付いていないので、コメントで探す
# 方針: 各タブのdiv[id=tab-XXX]の中のinline-rule-sectionを探す

for tab_key, rules_tab_id in TAB_MAP.items():
    # index.htmlのタブコンテンツdiv（main-tab-content）を探す
    # jidou -> tab-jidou, assistant -> tab-assistant, etc.
    main_tab_id = f"tab-{tab_key}"
    tab_div = soup.find("div", {"id": main_tab_id})
    if not tab_div:
        print(f"[WARN] tab div not found: {main_tab_id}")
        continue

    # inline-rule-sectionを探す（idがinline-rule-{tab_key}のものを優先）
    rule_section = tab_div.find("div", {"id": f"inline-rule-{tab_key}"})
    if not rule_section:
        # idなしのinline-rule-sectionを探す
        rule_section = tab_div.find("div", class_="inline-rule-section")

    if not rule_section:
        print(f"[WARN] inline-rule-section not found in {main_tab_id}")
        continue

    print(f"[OK] Found inline-rule-section in {main_tab_id}, replacing with placeholder...")

    # プレースホルダーHTMLを作成
    placeholder = BeautifulSoup(f"""
<div class="inline-rule-section" id="inline-rule-{tab_key}">
  <h4>📚 この機能のルール</h4>
  <div id="inline-rule-content-{tab_key}" style="color:#94a3b8; font-size:12px; padding:8px;">ルール読み込み中...</div>
</div>
""", "html.parser")

    rule_section.replace_with(placeholder)
    print(f"[OK] Replaced {main_tab_id}")

# 非表示バックアップのdivも削除（display:noneのinline-rule-card）
for hidden_card in soup.find_all("div", class_="inline-rule-card", style=lambda s: s and "display:none" in s):
    # その親がinline-rule-sectionでないことを確認
    parent = hidden_card.parent
    if parent and "inline-rule-section" not in parent.get("class", []):
        hidden_card.decompose()
        print("[OK] Removed hidden backup inline-rule-card")

result = str(soup)

# BeautifulSoupがHTMLエンティティをエスケープしてしまう場合があるので確認
with open("/home/ubuntu/notion-freee-auto/templates/index.html", "w", encoding="utf-8") as f:
    f.write(result)

print("Done!")
