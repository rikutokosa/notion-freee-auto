"""app.pyのapi_assistant_ai関数をFunction Calling対応版に置き換えるスクリプト"""

new_func = '''@app.route("/api/assistant/ai", methods=["POST"])
def api_assistant_ai():
    """自然言語指示をFunction Calling対応のAIエージェントで処理する"""
    import os, json as _json, re
    from datetime import date as _date
    data = request.get_json() or {}
    user_message = data.get("message", "")
    history = data.get("history", [])
    master = data.get("master", {})

    try:
        import requests as req
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        openai_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        if not openai_key:
            return jsonify({"error": "OpenAI APIキーが設定されていません"}), 500

        # 現在日付・年度情報
        today = _date.today()
        today_str = today.strftime("%Y年%m月%d日")
        fiscal_year = today.year if today.month >= 4 else today.year - 1
        fiscal_start = f"{fiscal_year}-04-01"
        fiscal_end = f"{fiscal_year + 1}-03-31"

        # マスタデータ
        sections_list = ", ".join([s["name"] for s in master.get("sections", [])])
        tags_list = ", ".join([t["name"] for t in master.get("tags", [])])
        account_items_list = ", ".join([a["name"] for a in master.get("account_items", [])])
        partners_list = ", ".join([p["name"] for p in master.get("partners", [])])

        system_prompt = f"""あなたはfreee会計のエキスパートアシスタントです。
ユーザーの自然言語の指示を正確に解釈し、提供されたツールを使って実行してください。

【現在の日付】{today_str}
【今年度】{fiscal_year}年4月〜{fiscal_year+1}年3月（{fiscal_start} 〜 {fiscal_end}）

【freeeマスタデータ】
取引先: {partners_list}
部門: {sections_list}
メモタグ: {tags_list}
勘定科目: {account_items_list}

【重要なルール】
- 取引先名はマスタの一覧から最も近い名前を推測して使用する（カタカナ・英語・表記ゆれも正しく対応）
- 日付は必ず現在の年度を基準に解釈する（「7月以降」→{fiscal_year}年7月以降）
- 削除指示の場合はまずsearch_deals/search_invoicesで対象を検索し、ユーザーに削除内容を伝えてからdelete_deals/delete_invoicesを呼び出す
- 登録指示の場合はマスタの勘定科目・取引先を正確に選んでregister_dealを呼び出す
- 不明な点があれば自分で推測して実行する（ユーザーに質問する前に試みる）"""

        # Function Callingのツール定義
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_deals",
                    "description": "freeeの仕訳（取引）を検索する。削除対象の確認や存在確認に使用する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "partner_name": {"type": "string", "description": "取引先名（部分一致）"},
                            "start_issue_date": {"type": "string", "description": "発生日開始（YYYY-MM-DD）"},
                            "end_issue_date": {"type": "string", "description": "発生日終了（YYYY-MM-DD）"},
                            "deal_type": {"type": "string", "enum": ["income", "expense"], "description": "取引種別"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_invoices",
                    "description": "freeeの請求書を検索する。削除対象の確認や存在確認に使用する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "partner_name": {"type": "string", "description": "取引先名（部分一致）"},
                            "start_issue_date": {"type": "string", "description": "発生日開始（YYYY-MM-DD）"},
                            "end_issue_date": {"type": "string", "description": "発生日終了（YYYY-MM-DD）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_deal",
                    "description": "freeeに仕訳（取引）を登録する。",
                    "parameters": {
                        "type": "object",
                        "required": ["deal_type", "issue_date", "details"],
                        "properties": {
                            "deal_type": {"type": "string", "enum": ["income", "expense"]},
                            "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "partner_name": {"type": "string"},
                            "details": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["account_item_name", "amount", "tax_code"],
                                    "properties": {
                                        "account_item_name": {"type": "string"},
                                        "amount": {"type": "integer", "description": "税抜金額"},
                                        "tax_code": {"type": "integer", "description": "1=課税売上10%, 7=課税仕入10%, 0=不課税"},
                                        "section_name": {"type": "string"},
                                        "tag_names": {"type": "array", "items": {"type": "string"}},
                                        "description": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_deals",
                    "description": "指定したIDの仕訳を削除する。必ず事前にsearch_dealsで対象を確認し、ユーザーに削除内容を伝えてから呼び出すこと。",
                    "parameters": {
                        "type": "object",
                        "required": ["deal_ids", "confirmation_message"],
                        "properties": {
                            "deal_ids": {"type": "array", "items": {"type": "integer"}, "description": "削除する仕訳IDのリスト"},
                            "confirmation_message": {"type": "string", "description": "ユーザーに表示する削除内容の説明"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_invoices",
                    "description": "指定したIDの請求書を削除する。必ず事前にsearch_invoicesで対象を確認し、ユーザーに削除内容を伝えてから呼び出すこと。",
                    "parameters": {
                        "type": "object",
                        "required": ["invoice_ids", "confirmation_message"],
                        "properties": {
                            "invoice_ids": {"type": "array", "items": {"type": "integer"}, "description": "削除する請求書IDのリスト"},
                            "confirmation_message": {"type": "string", "description": "ユーザーに表示する削除内容の説明"},
                        },
                    },
                },
            },
        ]

        # ツール実行関数
        def execute_tool(name, args):
            if name == "search_deals":
                deals = search_deals(**args)
                return _json.dumps([
                    {"id": d["id"], "issue_date": d.get("issue_date"), "partner_name": d.get("partner_name"),
                     "amount": d.get("amount"), "type": d.get("type")}
                    for d in deals
                ], ensure_ascii=False)
            elif name == "search_invoices":
                invoices = search_invoices(**args)
                return _json.dumps([
                    {"id": inv["id"], "issue_date": inv.get("issue_date"), "partner_name": inv.get("partner_name"),
                     "total_amount": inv.get("total_amount"), "invoice_number": inv.get("invoice_number")}
                    for inv in invoices
                ], ensure_ascii=False)
            elif name == "register_deal":
                cache = get_master_cache()
                deal_type = args.pop("deal_type")
                result = create_deal(args, deal_type, cache)
                return _json.dumps({"status": "ok", "id": result.get("id")}, ensure_ascii=False)
            elif name == "delete_deals":
                # 削除はユーザー確認が必要なので、削除内容を返すのみ（実行はフロントエンドで行う）
                return _json.dumps({
                    "status": "pending_confirmation",
                    "deal_ids": args["deal_ids"],
                    "message": args["confirmation_message"]
                }, ensure_ascii=False)
            elif name == "delete_invoices":
                return _json.dumps({
                    "status": "pending_confirmation",
                    "invoice_ids": args["invoice_ids"],
                    "message": args["confirmation_message"]
                }, ensure_ascii=False)
            return "{}"

        # エージェントループ（最大6回までツール呼び出しを繰り返す）
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-6:]:
            messages.append(h)
        messages.append({"role": "user", "content": user_message})

        pending_deletes = {"deal_ids": [], "invoice_ids": [], "message": ""}
        registered_ids = []
        final_reply = ""

        for _ in range(6):
            resp = req.post(
                f"{openai_base}/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o", "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.1},
                timeout=60,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            msg = choice["message"]
            messages.append(msg)

            # ツール呼び出しがなければ終了
            if not msg.get("tool_calls"):
                final_reply = msg.get("content", "")
                break

            # 各ツールを実行
            for tc in msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                fn_args = _json.loads(tc["function"]["arguments"])
                tool_result = execute_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

                # 削除待機中の場合はフロントに渡す
                result_obj = _json.loads(tool_result)
                if result_obj.get("status") == "pending_confirmation":
                    if "deal_ids" in result_obj:
                        pending_deletes["deal_ids"].extend(result_obj["deal_ids"])
                    if "invoice_ids" in result_obj:
                        pending_deletes["invoice_ids"].extend(result_obj["invoice_ids"])
                    pending_deletes["message"] = result_obj.get("message", "")

                # 登録成功の場合
                if fn_name == "register_deal" and result_obj.get("status") == "ok":
                    registered_ids.append(result_obj.get("id"))

        return jsonify({
            "reply": final_reply,
            "entries": [],
            "delete_actions": [],
            "pending_deletes": pending_deletes if (pending_deletes["deal_ids"] or pending_deletes["invoice_ids"]) else None,
            "registered_ids": registered_ids,
        })

    except Exception as e:
        logger.exception("AIエージェントエラー")
        return jsonify({"error": str(e)}), 500

'''

with open('/home/ubuntu/notion-freee-auto/app.py', 'r') as f:
    lines = f.readlines()

# 674行目(0-indexed: 673)から795行目(0-indexed: 794)を置き換え
start_idx = 673  # 0-indexed: @app.route("/api/assistant/ai"
end_idx = 795    # 0-indexed: exclusive (次の@app.routeの前まで)

new_lines = lines[:start_idx] + [new_func] + lines[end_idx:]
with open('/home/ubuntu/notion-freee-auto/app.py', 'w') as f:
    f.writelines(new_lines)
print(f"Done: replaced lines {start_idx+1}-{end_idx} with new Function Calling implementation")
