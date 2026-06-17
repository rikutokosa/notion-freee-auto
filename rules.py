"""
仕訳ルールエンジン
集客経路（求人データベース）・処理ステータスに応じてfreeeへの登録内容を決定する

対応ステータス:
  - 本部確認済: 通常の売上＋仕入仕訳（または請求書）登録
  - ●入社済: 請求書送付（要請求の場合のみ）
  - ●入社前辞退: 元の取引を削除
  - ●返金（短期離職）: 返金率に応じたマイナス仕訳を追加登録

請求有無の判定:
  - Notionの「請求有無」フォーミュラフィールドが「要請求」の場合は請求書登録
  - 「請求不要」の場合は仕訳登録のみ
  - フィールドが存在しない場合はRULESのneeds_invoiceで判定（後方互換）
"""
from datetime import date
from dateutil.relativedelta import relativedelta
import calendar
from typing import Optional


# ============================================================
# 集客経路ごとのルール定義
# キー = Notionの「求人データベース」selectの実際の値
# ============================================================
RULES = {
    # --- 求人DB（新しいプロパティ名） ---
    "Circus": {
        "type": "求人DB",
        "supplier": "circus株式会社",
        "payment_rule": "入社翌々月10日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "Zキャリア": {
        "type": "求人DB",
        "supplier": "株式会社ROXX",
        "payment_rule": "入社翌々月10日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "クラウドエージェント": {
        "type": "求人DB",
        "supplier": "株式会社Grooves",
        "payment_rule": "入社翌々月4日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "マイナビJOBシェアリング": {
        "type": "求人DB",
        "supplier": "株式会社マイナビ",
        "payment_rule": "入社翌月末",
        "billing_type": "請求書登録",
        "needs_invoice": True,
    },
    "Bee": {
        "type": "求人DB",
        "supplier": "株式会社ネオキャリア",
        "payment_rule": "入社翌月末",
        "billing_type": "請求書登録",
        "needs_invoice": True,
    },
    # CSS求人はスカウト手数料のみ登録（売上仕訳のみ、仕入なし）
    "CSS求人": {
        "type": "求人DB",
        "supplier": None,
        "payment_rule": "登録不要",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "本店自社求人": {
        "type": "求人DB",
        "supplier": None,
        "payment_rule": "都度確認",
        "billing_type": "請求書登録",
        "needs_invoice": True,
    },
    "Hitolink": {
        "type": "求人DB",
        "supplier": None,
        "payment_rule": "入社翌月末",
        "billing_type": "申請フォーム",
        "needs_invoice": False,
    },
    # --- 旧キー名（後方互換のため残す） ---
    "Circus | 請求不要": {
        "type": "求人DB",
        "supplier": "circus株式会社",
        "payment_rule": "入社翌々月10日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "Zキャリア | 請求書不要": {
        "type": "求人DB",
        "supplier": "株式会社ROXX",
        "payment_rule": "入社翌々月10日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "クラウドエージェント│請求不要": {
        "type": "求人DB",
        "supplier": "株式会社Grooves",
        "payment_rule": "入社翌々月4日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "CSS自社求人│スカウト手数料のみ登録": {
        "type": "求人DB",
        "supplier": None,
        "payment_rule": "登録不要",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "CSS自社求人│請求不要": {
        "type": "求人DB",
        "supplier": None,
        "payment_rule": "登録不要",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    # --- 集客 ---
    "RDS": {
        "type": "集客",
        "supplier": "株式会社インディードリクルートパートナーズ",
        "payment_rule": "入社翌々月末日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "マイナビ転職": {
        "type": "集客",
        "supplier": "株式会社マイナビ",
        "payment_rule": "入社翌々月10日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "dodaX": {
        "type": "集客",
        "supplier": "パーソルキャリア株式会社",
        "payment_rule": "入社翌月末",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "キミナラ": {
        "type": "集客",
        "supplier": "株式会社キミナラ",
        "payment_rule": "入社翌月末",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "ワンキャリア": {
        "type": "集客",
        "supplier": "株式会社ワンキャリア",
        "payment_rule": "入社翌月末",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "openwork": {
        "type": "集客",
        "supplier": None,
        "payment_rule": "入社翌月末",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    "tezuna": {
        "type": "集客",
        "supplier": None,
        "payment_rule": "入社翌月末",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
    # --- パートナー ---
    "PCA": {
        "type": "パートナー",
        "supplier": None,  # 担当パートナー依存（マイネーム・立野など）
        "payment_rule": "入社翌々月末日",
        "billing_type": "仕訳登録のみ",
        "needs_invoice": False,
    },
}


def calc_payment_date(nyusha_date: date, rule: str) -> Optional[date]:
    """
    入社日と支払ルールから決済期日を計算する
    """
    if not nyusha_date or rule in ("登録不要", "都度確認", "申請フォーム"):
        return None

    if rule == "入社翌々月10日":
        d = nyusha_date + relativedelta(months=2)
        return d.replace(day=10)
    elif rule == "入社翌々月4日":
        d = nyusha_date + relativedelta(months=2)
        return d.replace(day=4)
    elif rule == "入社翌々月末日":
        d = nyusha_date + relativedelta(months=2)
        last_day = calendar.monthrange(d.year, d.month)[1]
        return d.replace(day=last_day)
    elif rule == "入社翌月末":
        d = nyusha_date + relativedelta(months=1)
        last_day = calendar.monthrange(d.year, d.month)[1]
        return d.replace(day=last_day)
    elif rule == "入社翌月10日":
        d = nyusha_date + relativedelta(months=1)
        return d.replace(day=10)

    return None


def get_rule(job_db_value: str) -> Optional[dict]:
    """
    求人データベースのselect値からルールを取得する
    完全一致 → 部分一致の順で検索
    """
    if not job_db_value:
        return None

    # 完全一致
    if job_db_value in RULES:
        return RULES[job_db_value]

    # 部分一致（キーが含まれているか、または値がキーに含まれているか）
    for key, rule in RULES.items():
        if key in job_db_value or job_db_value in key:
            return rule

    return None


def _extract_props(record: dict) -> dict:
    """
    Notionレコードから主要プロパティを抽出する
    """
    from datetime import datetime as dt

    props = record.get("properties", {})

    def get_select(key: str) -> Optional[str]:
        info = props.get(key, {})
        sel = info.get("select")
        return sel.get("name") if sel else None

    def get_date(key: str) -> Optional[str]:
        info = props.get(key, {})
        t = info.get("type", "")
        if t == "date":
            d = info.get("date")
            return d.get("start") if d else None
        elif t == "created_time":
            return info.get("created_time", "")[:10]
        elif t == "formula":
            f = info.get("formula", {})
            ft = f.get("type", "")
            if ft == "string":
                return f.get("string")
            elif ft == "date":
                d = f.get("date")
                return d.get("start") if d else None
        return None

    def get_number(key: str) -> Optional[float]:
        info = props.get(key, {})
        t = info.get("type", "")
        if t == "number":
            return info.get("number")
        elif t == "formula":
            f = info.get("formula", {})
            ft = f.get("type", "")
            if ft == "number":
                return f.get("number")
        return None

    def get_title(key: str) -> str:
        info = props.get(key, {})
        texts = info.get("title", [])
        return "".join([x.get("plain_text", "") for x in texts])

    def get_rich_text(key: str) -> str:
        info = props.get(key, {})
        texts = info.get("rich_text", [])
        return "".join([x.get("plain_text", "") for x in texts])

    def get_formula_string(key: str) -> Optional[str]:
        """フォーミュラ型の文字列値を取得する"""
        info = props.get(key, {})
        t = info.get("type", "")
        if t == "formula":
            f = info.get("formula", {})
            ft = f.get("type", "")
            if ft == "string":
                return f.get("string")
        elif t == "select":
            sel = info.get("select")
            return sel.get("name") if sel else None
        elif t == "rich_text":
            texts = info.get("rich_text", [])
            return "".join([x.get("plain_text", "") for x in texts]) or None
        return None

    def get_rollup_select(key: str) -> Optional[str]:
        """rollup(array of select)から最初の値を取得する"""
        info = props.get(key, {})
        rollup = info.get("rollup", {})
        arr = rollup.get("array", [])
        for item in arr:
            if item.get("type") == "select":
                sel = item.get("select")
                if sel:
                    return sel.get("name")
        return None

    def parse_date(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return dt.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    phase = get_title("フェーズ")
    tanto_ca = get_rollup_select("担当CA")
    job_db = get_select("求人データベース")
    shukyaku_keiro = get_rollup_select("集客経路")  # rollupから取得
    current_status = get_select("請求ステータス")
    nyusha_str = get_date("入社日")
    seiyaku_str = get_date("成約日")
    zeinuki_uriage = get_number("税抜売上")
    zeinuki_shukyaku = get_number("税抜集客手数料")
    uriage_kessai_str = get_date("売上決済期日")
    shiire_kessai_str = get_date("仕入決済期日")
    henkin_ritsu_raw = get_number("返金率")
    # 返金後入金売上（返金後の実際の売上額）
    henkin_go_uriage = get_number("返金後入金売上")
    henkin_go_shukyaku = get_number("返金後集客手数料")
    # 既存のfreee取引ID（入社前辞退・返金時の削除に使用）
    freee_sales_id = get_number("freee売上取引ID")
    freee_purchase_id = get_number("freee支出取引ID")
    # PCA専用: PCA仕入高（パートナーへの支払）
    pca_shiire = get_number("PCA仕入高")
    pca_kessai_str = get_date("PCA仕入決済期日")
    # 請求有無フィールド（フォーミュラ型）
    invoice_required_str = get_formula_string("請求有無")

    # 求職者名を取得（relationから別ページを参照）
    from notion_client import get_jobseeker_name, get_company_name
    jobseeker_name = get_jobseeker_name(record)
    # 入社企業名を取得（決定企業（DB）relationから）
    company_name = get_company_name(record)

    return {
        "phase": phase,
        "job_db": job_db,
        "current_status": current_status,
        "nyusha_str": nyusha_str,
        "nyusha_date": parse_date(nyusha_str),
        "seiyaku_str": seiyaku_str,
        "seiyaku_date": parse_date(seiyaku_str),
        "zeinuki_uriage": zeinuki_uriage,
        "zeinuki_shukyaku": zeinuki_shukyaku,
        "uriage_kessai": parse_date(uriage_kessai_str),
        "shiire_kessai": parse_date(shiire_kessai_str),
        "henkin_ritsu": henkin_ritsu_raw or 0,
        "henkin_go_uriage": henkin_go_uriage,
        "henkin_go_shukyaku": henkin_go_shukyaku,
        "freee_sales_id": int(freee_sales_id) if freee_sales_id else None,
        "freee_purchase_id": int(freee_purchase_id) if freee_purchase_id else None,
        "pca_shiire": pca_shiire,
        "pca_kessai": parse_date(pca_kessai_str),
        "db_type": record.get("_db_type", "honten"),
        "tanto_ca": tanto_ca,
        "jobseeker_name": jobseeker_name,
        "company_name": company_name,
        "shukyaku_keiro": shukyaku_keiro,
        "invoice_required_str": invoice_required_str,  # 「要請求」または「請求不要」
    }


def build_journal_entries(record: dict) -> dict:
    """
    Notionレコードから処理内容を構築する

    返り値:
    {
        "action": "register" | "delete" | "refund" | "skip" | "review" | "error" | "send_invoice",
        "message": str,
        "sales_entry": dict | None,
        "purchase_entry": dict | None,
        "pca_entry": dict | None,
        "delete_sales_id": int | None,
        "delete_purchase_id": int | None,
        "needs_invoice": bool,
        "rule": dict | None,
        "job_db": str,
        "nyusha_date": str,
        "phase": str,
        "original_status": str,
    }
    """
    p = _extract_props(record)
    phase = p["phase"]
    job_db = p["job_db"] or ""
    current_status = p["current_status"] or ""
    db_type = p["db_type"]

    base = {
        "action": None,
        "message": "",
        "sales_entry": None,
        "purchase_entry": None,
        "pca_entry": None,
        "delete_sales_id": None,
        "delete_purchase_id": None,
        "needs_invoice": False,
        "rule": None,
        "job_db": job_db,
        "nyusha_date": p["nyusha_str"] or "",
        "phase": phase,
        "original_status": current_status,
    }

    # ============================================================
    # ① 入社前辞退: 元の取引を削除
    # ============================================================
    if current_status == "●入社前辞退":
        if not p["freee_sales_id"] and not p["freee_purchase_id"]:
            base["action"] = "review"
            base["message"] = "入社前辞退ですが、freee取引IDが見つかりません。手動で確認してください。"
            return base

        base["action"] = "delete"
        base["message"] = f"入社前辞退: 取引を削除します（売上ID={p['freee_sales_id']}, 仕入ID={p['freee_purchase_id']}）"
        base["delete_sales_id"] = p["freee_sales_id"]
        base["delete_purchase_id"] = p["freee_purchase_id"]
        return base

    # ============================================================
    # ② 返金（短期離職）: マイナス仕訳を追加登録
    # ============================================================
    if current_status == "●返金（短期離職）":
        henkin_ritsu = p["henkin_ritsu"]
        if henkin_ritsu <= 0:
            base["action"] = "review"
            base["message"] = "返金ですが、返金率が0%または未設定です。手動で確認してください。"
            return base

        original_uriage = p["zeinuki_uriage"] or 0
        original_shukyaku = p["zeinuki_shukyaku"] or 0

        henkin_uriage = p["henkin_go_uriage"]
        henkin_shukyaku = p["henkin_go_shukyaku"]

        if henkin_uriage is not None:
            minus_uriage = henkin_uriage - original_uriage  # 負の値
        else:
            minus_uriage = -int(original_uriage * henkin_ritsu / 100)

        if henkin_shukyaku is not None:
            minus_shukyaku = henkin_shukyaku - original_shukyaku  # 負の値
        else:
            minus_shukyaku = -int(original_shukyaku * henkin_ritsu / 100)

        today_str = date.today().isoformat()

        # マイナス売上仕訳
        if minus_uriage != 0:
            base["sales_entry"] = {
                "issue_date": today_str,
                "due_date": None,
                "partner_name": None,
                "details": [{
                    "account_item_name": "CA売上【自社】",
                    "tax_code": 129,
                    "amount": int(minus_uriage),  # 負の値
                    "description": f"返金 {phase}",
                    "item_name": "本店：CA",
                    "tag_names": [],
                }],
                "memo": f"返金 {phase}",
            }

        # マイナス仕入仕訳
        rule = get_rule(job_db)
        if rule and rule["payment_rule"] != "登録不要" and minus_shukyaku != 0:
            base["purchase_entry"] = {
                "issue_date": today_str,
                "due_date": None,
                "partner_name": rule.get("supplier"),
                "details": [{
                    "account_item_name": "スカウト手数料",
                    "tax_code": 136,
                    "amount": int(minus_shukyaku),  # 負の値
                    "description": f"返金 {phase}",
                    "item_name": "本店：CA",
                    "tag_names": [],
                }],
                "memo": f"返金 {phase}",
            }

        # PCA成約管理の場合: PCA支払のマイナス仕訳も追加
        if db_type == "pca" and p["pca_shiire"]:
            pca_henkin = -int(p["pca_shiire"] * henkin_ritsu / 100)
            if pca_henkin != 0:
                base["pca_entry"] = {
                    "issue_date": today_str,
                    "due_date": None,
                    "partner_name": None,
                    "details": [{
                    "account_item_name": "受け取り報酬料",
                    "tax_code": 136,
                        "amount": pca_henkin,
                        "description": f"返金 {phase}",
                        "item_name": "本店：CA",
                        "tag_names": [],
                    }],
                    "memo": f"返金 {phase}",
                }

        base["action"] = "refund"
        base["message"] = f"返金処理: 売上マイナス={minus_uriage}円, 仕入マイナス={minus_shukyaku}円"
        base["rule"] = rule
        return base

    # ============================================================
    # ③ 入社済: 請求書送付（要請求の場合のみ）
    # ============================================================
    if current_status == "●入社済":
        # 請求有無を確認（フォーミュラフィールドまたはRULESで判定）
        invoice_required = _is_invoice_required(p, job_db)
        if not invoice_required:
            # 請求不要の場合は何もしない（スキップ）
            base["action"] = "skip"
            base["message"] = "入社済・請求不要のためスキップ"
            return base

        base["action"] = "send_invoice"
        base["message"] = f"入社済: 請求書を送付します（freee請求書IDが必要）"
        base["needs_invoice"] = True
        return base

    # ============================================================
    # ④ 本部確認済: 通常の売上＋仕入仕訳登録
    # ============================================================
    if current_status != "本部確認済":
        base["action"] = "review"
        base["message"] = f"未対応のステータス「{current_status}」です。手動で確認してください。"
        return base

    # ルール取得
    rule = get_rule(job_db)
    if not rule:
        base["action"] = "review"
        base["message"] = f"求人データベース「{job_db}」に対応するルールが見つかりません。手動で確認してください。"
        return base

    base["rule"] = rule

    # 都度確認・申請フォームは手動対応
    if rule["payment_rule"] == "都度確認":
        base["action"] = "review"
        base["message"] = f"「{job_db}」は決済期日を都度確認が必要です。手動で処理してください。"
        return base

    if rule["billing_type"] == "申請フォーム":
        base["action"] = "review"
        base["message"] = f"「{job_db}」は申請フォームでの処理が必要です。手動で処理してください。"
        return base

    # 請求有無を判定（Notionの「請求有無」フィールド優先、なければRULESで判定）
    needs_invoice = _is_invoice_required(p, job_db)

    # 決済期日（Notionのformulaが取れない場合は自前計算）
    uriage_kessai = p["uriage_kessai"]
    shiire_kessai = p["shiire_kessai"]
    if not shiire_kessai and p["nyusha_date"]:
        shiire_kessai = calc_payment_date(p["nyusha_date"], rule["payment_rule"])

    # 発生日: 入社日を使用（入社日がない場合は今日）
    nyusha_date_str = p["nyusha_str"][:10] if p["nyusha_str"] else date.today().isoformat()
    issue_date = nyusha_date_str

    # 備考: 求職者名 + 入社企業名
    jobseeker_name = p.get("jobseeker_name") or ""
    company_name = p.get("company_name") or ""
    biko_parts = [x for x in [jobseeker_name, company_name] if x]
    biko = " ".join(biko_parts)

    # メモタグ: 担当CA名をメモタグに設定
    tanto_ca = p.get("tanto_ca") or ""
    tag_names = [tanto_ca] if tanto_ca else []

    # 売上仕訳
    if p["zeinuki_uriage"] and p["zeinuki_uriage"] > 0:
        account_item = "CA売上【自社】"
        if db_type == "pca":
            account_item = "PCA売上"

        # 売上取引先: 求人データベース型の場合は取引先を設定（集客型は売上に取引先なし）
        if rule.get("type") == "求人DB":
            sales_partner = rule.get("supplier")
        else:
            sales_partner = None

        # 部門設定: PCAは「本店：PCA」、本店CAは「本店：CA」（freeeの正式部門名）
        section_name = "本店：PCA" if db_type == "pca" else "本店：CA"

        base["sales_entry"] = {
            "issue_date": issue_date,
            "due_date": uriage_kessai.isoformat() if uriage_kessai else None,
            "partner_name": sales_partner,
            "section_name": section_name,
            "details": [{
                "account_item_name": account_item,
                "tax_code": 129,
                "amount": int(p["zeinuki_uriage"]),
                "description": biko,
                "tag_names": tag_names,
            }],
            "memo": biko,
        }

    # 仕入仕訳（登録不要の場合はスキップ）
    if rule["payment_rule"] != "登録不要":
        if p["zeinuki_shukyaku"] and p["zeinuki_shukyaku"] > 0:
            purchase_section = "本店：PCA" if db_type == "pca" else "本店：CA"
            shukyaku_rule = get_rule(p.get("shukyaku_keiro") or "")
            purchase_partner = shukyaku_rule.get("supplier") if shukyaku_rule else rule.get("supplier")
            base["purchase_entry"] = {
                "issue_date": nyusha_date_str,
                "due_date": shiire_kessai.isoformat() if shiire_kessai else None,
                "partner_name": purchase_partner,
                "section_name": purchase_section,
                "details": [{
                    "account_item_name": "スカウト手数料",
                    "tax_code": 136,
                    "amount": int(p["zeinuki_shukyaku"]),
                    "description": biko,
                    "tag_names": tag_names,
                }],
                "memo": biko,
            }

    # PCA成約管理の場合: パートナーへの支払仕訳も追加
    if db_type == "pca" and p["pca_shiire"] and p["pca_shiire"] > 0:
        pca_kessai = p["pca_kessai"]
        if not pca_kessai and p["nyusha_date"]:
            pca_kessai = calc_payment_date(p["nyusha_date"], "入社翌々月末日")

        base["pca_entry"] = {
            "issue_date": nyusha_date_str,
            "due_date": pca_kessai.isoformat() if pca_kessai else None,
            "partner_name": None,
            "section_name": "本店：PCA",
            "details": [{
                    "account_item_name": "PCA仕入高",
                    "tax_code": 136,
                "amount": int(p["pca_shiire"]),
                "description": biko,
                "tag_names": tag_names,
            }],
            "memo": biko,
        }

    # 仕入登録不要の場合
    if rule["payment_rule"] == "登録不要":
        base["action"] = "register_sales_only"
        base["message"] = f"「{job_db}」は仕入仕訳登録不要です。売上仕訳のみ登録します。"
        base["needs_invoice"] = needs_invoice
        return base

    base["action"] = "register"
    base["message"] = "仕訳データを生成しました"
    base["needs_invoice"] = needs_invoice
    return base


def _is_invoice_required(p: dict, job_db: str) -> bool:
    """
    請求書が必要かどうかを判定する
    Notionの「請求有無」フォーミュラフィールドが「要請求」の場合はTrue
    フィールドが存在しない場合はRULESのneeds_invoiceで判定（後方互換）
    """
    invoice_required_str = p.get("invoice_required_str")
    if invoice_required_str is not None:
        return invoice_required_str == "要請求"

    # フィールドが存在しない場合はRULESで判定
    rule = get_rule(job_db)
    if rule:
        return rule.get("needs_invoice", False)
    return False
