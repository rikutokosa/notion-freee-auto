"""
rules.htmlを再構築する:
- 108行目以降の古いタブ内容を全部削除
- {% include 'rules_tabs.html' %} + {% endblock %} + {% block scripts %} ... {% endblock %} だけ残す
"""

with open("/home/ubuntu/notion-freee-auto/templates/rules.html", "r", encoding="utf-8") as f:
    lines = f.readlines()

# 1〜107行目を保持（タブボタンまで）
# ただし107行目以降は {% include 'rules_tabs.html' %} に置き換える

# 107行目まで（0-indexed: 0〜106）を保持
header_lines = lines[:107]  # 行1〜107

# {% include 'rules_tabs.html' %} を追加
include_line = "\n  {% include 'rules_tabs.html' %}\n\n</div>\n{% endblock %}\n\n{% block scripts %}\n<script>\nfunction showTab(id, btn) {\n  document.querySelectorAll('.rules-section').forEach(s => s.classList.remove('active'));\n  document.querySelectorAll('.rules-tab-btn').forEach(b => b.classList.remove('active'));\n  document.getElementById(id).classList.add('active');\n  if (btn) btn.classList.add('active');\n  location.hash = id;\n}\n\n// URLハッシュがあれば対応タブを開く\ndocument.addEventListener('DOMContentLoaded', function() {\n  const hash = location.hash.replace('#', '');\n  if (hash) {\n    const target = document.getElementById(hash);\n    if (target) {\n      document.querySelectorAll('.rules-section').forEach(s => s.classList.remove('active'));\n      document.querySelectorAll('.rules-tab-btn').forEach(b => b.classList.remove('active'));\n      target.classList.add('active');\n      // 対応するボタンをアクティブに\n      document.querySelectorAll('.rules-tab-btn').forEach(b => {\n        if (b.getAttribute('onclick') && b.getAttribute('onclick').includes(hash)) {\n          b.classList.add('active');\n        }\n      });\n    }\n  }\n});\n</script>\n{% endblock %}\n"

# 107行目の {% include 'rules_tabs.html' %} コメントを確認
print("Line 107:", repr(lines[106]))
print("Line 108:", repr(lines[107]))

# 107行目が既に {% include 'rules_tabs.html' %} になっているか確認
if "{% include 'rules_tabs.html' %}" in lines[106]:
    print("Line 107 already has include, rebuilding from line 108...")
    new_content = "".join(header_lines) + include_line
else:
    print("Rebuilding from line 107...")
    new_content = "".join(header_lines[:106]) + include_line

with open("/home/ubuntu/notion-freee-auto/templates/rules.html", "w", encoding="utf-8") as f:
    f.write(new_content)

print("Done! New rules.html line count:", len(new_content.splitlines()))
