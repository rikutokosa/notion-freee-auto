"""
tests/test_rules.py
build_journal_entries が代表的な Notion レコードに対して
期待する action / amount / due_date / partner を返すことを確認する。

本番 freee / Notion / OpenAI は一切叩かない。
get_jobseeker_name / get_company_name は monkeypatch でモックする。
"""
import sys
import os
from datetime import date

import pytest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules import build_journal_entries, RULES


# ============================================================
# autouse fixture: 外部 API 呼び出しをモックする
# ============================================================

@pytest.fixture(autouse=True)
def mock_notion_name_lookups(monkeypatch):
    """
    get_jobseeker_name / get_company_name は Notion API を呼ぶため
    テスト環境では常にモックする。
    """
    import notion_client as nc
    monkeypatch.setattr(nc, "get_jobseeker_name", lambda record: "テスト太郎")
    monkeypatch.setattr(nc, "get_company_name", lambda record: "株式会社テスト")


# ============================================================
# ヘルパー: Notion レコードを組み立てる
# ============================================================

def _make_record(
    status: str,
    job_db: str = "Circus",
    nyusha_date: str = "2025-04-01",
    uriage: float = 500000.0,
    shukyaku: float = 100000.0,
    freee_sales_id: float | None = None,
    freee_purchase_id: float | None = None,
    invoice_required: str | None = None,
    db_type: str = "honten",
) -> dict:
    """
    テスト用 Notion レコードを生成する。
    _extract_props が実際に読むプロパティキー名・型に合わせる。

    重要なキー名:
      - 「請求ステータス」 select型  ← ステータス判定に使う
      - 「入社日」         date型
      - 「求人データベース」select型
      - 「税抜売上」       number型  ← type: "number" を明示
      - 「税抜集客手数料」 number型
      - 「freee売上取引ID」number型  ← 削除時に使う
      - 「freee仕入取引ID」number型
      - 「返金料率」       number型  ← 「返金率（%）」ではない
      - 「請求有無」       formula型 ← get_formula_string で取得
    """
    props: dict = {
        "名前": {"title": [{"plain_text": "テスト太郎"}]},
        # ステータスは「請求ステータス」（select型）
        "請求ステータス": {"type": "select", "select": {"name": status}},
        # 入社日（date型）
        "入社日": {"type": "date", "date": {"start": nyusha_date}},
        # 求人データベース（select型）
        "求人データベース": {"type": "select", "select": {"name": job_db}},
        # 税抜売上（number型）
        "税抜売上": {"type": "number", "number": uriage},
        # 税抜集客手数料（number型）
        "税抜集客手数料": {"type": "number", "number": shukyaku},
        # freee取引ID（number型）
        "freee売上取引ID": {"type": "number", "number": freee_sales_id},
        "freee仕入取引ID": {"type": "number", "number": freee_purchase_id},
        # freee請求書ID
        "freee請求書ID": {"type": "number", "number": None},
    }
    # 請求有無フィールド（フォーミュラ型）
    if invoice_required is not None:
        props["請求有無"] = {
            "type": "formula",
            "formula": {"type": "string", "string": invoice_required},
        }
    return {
        "id": "page-test-001",
        "_db_type": db_type,
        "properties": props,
    }


# ============================================================
# テスト: register（通常登録）
# ============================================================

class TestBuildJournalRegister:
    """本部確認済 → register アクション"""

    def test_action_is_register(self):
        record = _make_record("本部確認済", job_db="Circus")
        result = build_journal_entries(record)
        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )

    def test_sales_entry_amount(self):
        """税抜売上 500,000 → 税込 550,000 が sales_entry に入る"""
        record = _make_record("本部確認済", job_db="Circus", uriage=500000.0)
        result = build_journal_entries(record)
        assert result.get("sales_entry") is not None, "sales_entry が None"
        amount = result["sales_entry"]["details"][0]["amount"]
        assert amount == 550000, f"Expected 550000, got {amount}"

    def test_purchase_entry_amount(self):
        """税抜集客手数料 100,000 → 税込 110,000 が purchase_entry に入る"""
        record = _make_record("本部確認済", job_db="Circus", shukyaku=100000.0)
        result = build_journal_entries(record)
        assert result.get("purchase_entry") is not None, "purchase_entry が None"
        amount = result["purchase_entry"]["details"][0]["amount"]
        assert amount == 110000, f"Expected 110000, got {amount}"

    def test_sales_entry_issue_date(self):
        """sales_entry の issue_date は入社日と一致する"""
        record = _make_record("本部確認済", job_db="Circus", nyusha_date="2025-06-15")
        result = build_journal_entries(record)
        assert result.get("sales_entry") is not None
        assert result["sales_entry"]["issue_date"] == "2025-06-15"

    def test_needs_invoice_false_for_no_invoice_rule(self):
        """Circus は needs_invoice=False のルール → needs_invoice は False"""
        record = _make_record("本部確認済", job_db="Circus")
        result = build_journal_entries(record)
        assert result["needs_invoice"] is False

    def test_job_db_is_preserved(self):
        """job_db が結果に含まれる"""
        record = _make_record("本部確認済", job_db="Circus")
        result = build_journal_entries(record)
        assert result["job_db"] == "Circus"

    def test_nyusha_date_is_preserved(self):
        """nyusha_date が結果に含まれる"""
        record = _make_record("本部確認済", job_db="Circus", nyusha_date="2025-04-01")
        result = build_journal_entries(record)
        assert result["nyusha_date"] == "2025-04-01"

    def test_purchase_partner_is_supplier(self):
        """purchase_entry の partner_name は RULES の supplier と一致する"""
        record = _make_record("本部確認済", job_db="Circus")
        result = build_journal_entries(record)
        assert result.get("purchase_entry") is not None
        rule = RULES.get("Circus")
        assert rule is not None
        assert result["purchase_entry"]["partner_name"] == rule["supplier"]


# ============================================================
# テスト: delete（入社前辞退）
# ============================================================

class TestBuildJournalDelete:
    """●入社前辞退 → delete アクション"""

    def test_action_is_delete(self):
        record = _make_record(
            "●入社前辞退",
            job_db="Circus",
            freee_sales_id=12345.0,
            freee_purchase_id=67890.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "delete", (
            f"Expected delete, got {result['action']}: {result.get('message')}"
        )

    def test_delete_ids_are_set(self):
        record = _make_record(
            "●入社前辞退",
            job_db="Circus",
            freee_sales_id=12345.0,
            freee_purchase_id=67890.0,
        )
        result = build_journal_entries(record)
        assert result["delete_sales_id"] == 12345
        assert result["delete_purchase_id"] == 67890

    def test_delete_without_freee_id_returns_review(self):
        """freee ID がない場合は review を返す"""
        record = _make_record("●入社前辞退", job_db="Circus")
        result = build_journal_entries(record)
        assert result["action"] == "review"


# ============================================================
# テスト: send_invoice（入社済・要請求）
# ============================================================

class TestBuildJournalSendInvoice:
    """●入社済・要請求 → send_invoice アクション"""

    def test_action_is_send_invoice(self):
        record = _make_record(
            "●入社済",
            job_db="Circus",
            invoice_required="要請求",
        )
        result = build_journal_entries(record)
        assert result["action"] == "send_invoice", (
            f"Expected send_invoice, got {result['action']}: {result.get('message')}"
        )

    def test_skip_when_no_invoice_required(self):
        """請求不要の場合は skip"""
        record = _make_record(
            "●入社済",
            job_db="Circus",
            invoice_required="請求不要",
        )
        result = build_journal_entries(record)
        assert result["action"] == "skip", (
            f"Expected skip, got {result['action']}: {result.get('message')}"
        )


# ============================================================
# テスト: refund（返金）
# ============================================================

class TestBuildJournalRefund:
    """●返金（短期離職） → refund アクション"""

    def test_action_is_refund(self):
        """返金率が設定されていれば refund を返す"""
        props = {
            "名前": {"title": [{"plain_text": "テスト太郎"}]},
            "請求ステータス": {"type": "select", "select": {"name": "●返金（短期離職）"}},
            "入社日": {"type": "date", "date": {"start": "2025-04-01"}},
            "退職日•辞退日": {"type": "date", "date": {"start": "2025-05-01"}},
            "求人データベース": {"type": "select", "select": {"name": "Circus"}},
            "税抜売上": {"type": "number", "number": 500000.0},
            "税抜集客手数料": {"type": "number", "number": 100000.0},
            "返金料率": {"type": "number", "number": 50.0},
            "freee売上取引ID": {"type": "number", "number": None},
            "freee仕入取引ID": {"type": "number", "number": None},
            "freee請求書ID": {"type": "number", "number": None},
        }
        record = {"id": "page-refund-001", "_db_type": "honten", "properties": props}
        result = build_journal_entries(record)
        assert result["action"] == "refund", (
            f"Expected refund, got {result['action']}: {result.get('message')}"
        )

    def test_refund_zero_rate_returns_review(self):
        """返金率 0% かつ返金後金額未設定 → review"""
        props = {
            "名前": {"title": [{"plain_text": "テスト太郎"}]},
            "請求ステータス": {"type": "select", "select": {"name": "●返金（短期離職）"}},
            "入社日": {"type": "date", "date": {"start": "2025-04-01"}},
            "求人データベース": {"type": "select", "select": {"name": "Circus"}},
            "税抜売上": {"type": "number", "number": 500000.0},
            "税抜集客手数料": {"type": "number", "number": 100000.0},
            "返金料率": {"type": "number", "number": 0.0},
            "freee売上取引ID": {"type": "number", "number": None},
            "freee仕入取引ID": {"type": "number", "number": None},
            "freee請求書ID": {"type": "number", "number": None},
        }
        record = {"id": "page-refund-002", "_db_type": "honten", "properties": props}
        result = build_journal_entries(record)
        assert result["action"] == "review"


# ============================================================
# テスト: unknown status → review
# ============================================================

class TestBuildJournalUnknownStatus:
    """未知のステータス → review アクション"""

    def test_unknown_status_returns_review(self):
        record = _make_record("謎のステータス", job_db="Circus")
        result = build_journal_entries(record)
        assert result["action"] == "review"


# ============================================================
# テスト: unknown job_db → review
# ============================================================

class TestBuildJournalUnknownJobDb:
    """未知の求人DB → review アクション"""

    def test_unknown_job_db_returns_review(self):
        record = _make_record("本部確認済", job_db="存在しない求人DB")
        result = build_journal_entries(record)
        assert result["action"] == "review"
