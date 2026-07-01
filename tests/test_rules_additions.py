"""
tests/test_rules_additions.py
rules.py の追加安全化テスト
- needs_invoice=True のケース（RULESによる判定 / 請求有無フォーミュラによる判定）
- CSS求人 の register_scout_only ケース
- 支払期日 due_date の代表ケース
- 返金短期離職の返金金額計算ケース

本番 freee / Notion / OpenAI は一切叩かない。
"""
import sys
import os
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules import build_journal_entries, RULES, calc_payment_date


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
    henkin_ritsu: float | None = None,
    taishoku_date: str | None = None,
) -> dict:
    """テスト用 Notion レコードを生成する。"""
    props: dict = {
        "名前": {"title": [{"plain_text": "テスト太郎"}]},
        "請求ステータス": {"type": "select", "select": {"name": status}},
        "入社日": {"type": "date", "date": {"start": nyusha_date}},
        "求人データベース": {"type": "select", "select": {"name": job_db}},
        "税抜売上": {"type": "number", "number": uriage},
        "税抜集客手数料": {"type": "number", "number": shukyaku},
        "freee売上取引ID": {"type": "number", "number": freee_sales_id},
        "freee仕入取引ID": {"type": "number", "number": freee_purchase_id},
        "freee請求書ID": {"type": "number", "number": None},
    }
    if invoice_required is not None:
        props["請求有無"] = {
            "type": "formula",
            "formula": {"type": "string", "string": invoice_required},
        }
    if henkin_ritsu is not None:
        props["返金料率"] = {"type": "number", "number": henkin_ritsu}
    if taishoku_date is not None:
        props["退職日•辞退日"] = {"type": "date", "date": {"start": taishoku_date}}
    return {
        "id": "page-test-add-001",
        "_db_type": db_type,
        "properties": props,
    }


# ============================================================
# テスト: needs_invoice=True のケース
# ============================================================

class TestNeedsInvoiceTrue:
    """needs_invoice=True になるケースを検証する"""

    def test_rules_needs_invoice_true_for_minabi(self):
        """マイナビJOBシェアリングは RULES.needs_invoice=True → needs_invoice=True"""
        record = _make_record("本部確認済", job_db="マイナビJOBシェアリング")
        result = build_journal_entries(record)
        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )
        assert result["needs_invoice"] is True, (
            f"マイナビJOBシェアリングは needs_invoice=True のはず: got {result['needs_invoice']}"
        )

    def test_rules_needs_invoice_true_for_bee(self):
        """Bee は RULES.needs_invoice=True → needs_invoice=True"""
        record = _make_record("本部確認済", job_db="Bee")
        result = build_journal_entries(record)
        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )
        assert result["needs_invoice"] is True, (
            f"Bee は needs_invoice=True のはず: got {result['needs_invoice']}"
        )

    def test_formula_invoice_required_overrides_rules(self):
        """請求有無フォーミュラ=要請求 は RULES.needs_invoice=False を上書きする"""
        # Circus は RULES.needs_invoice=False だが、フォーミュラ=要請求なら True
        record = _make_record(
            "本部確認済",
            job_db="Circus",
            invoice_required="要請求",
        )
        result = build_journal_entries(record)
        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )
        assert result["needs_invoice"] is True, (
            f"フォーミュラ=要請求なら needs_invoice=True のはず: got {result['needs_invoice']}"
        )

    def test_formula_invoice_not_required_overrides_rules(self):
        """請求有無フォーミュラ=請求不要 は RULES.needs_invoice=True を上書きする"""
        # マイナビJOBシェアリングは RULES.needs_invoice=True だが、フォーミュラ=請求不要なら False
        record = _make_record(
            "本部確認済",
            job_db="マイナビJOBシェアリング",
            invoice_required="請求不要",
        )
        result = build_journal_entries(record)
        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )
        assert result["needs_invoice"] is False, (
            f"フォーミュラ=請求不要なら needs_invoice=False のはず: got {result['needs_invoice']}"
        )

    def test_send_invoice_action_when_invoice_required(self):
        """●入社済 + 請求有無=要請求 → send_invoice アクション"""
        record = _make_record(
            "●入社済",
            job_db="マイナビJOBシェアリング",
            invoice_required="要請求",
        )
        result = build_journal_entries(record)
        assert result["action"] == "send_invoice", (
            f"Expected send_invoice, got {result['action']}: {result.get('message')}"
        )


# ============================================================
# テスト: CSS求人 register_scout_only
# ============================================================

class TestCSSRegisterScoutOnly:
    """CSS求人は register_scout_only アクション"""

    def test_action_is_register_scout_only(self):
        """CSS求人 + 本部確認済 → register_scout_only"""
        record = _make_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "register_scout_only", (
            f"Expected register_scout_only, got {result['action']}: {result.get('message')}"
        )

    def test_css_has_purchase_entry_no_sales_entry(self):
        """CSS求人は purchase_entry あり・sales_entry なし"""
        record = _make_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = build_journal_entries(record)
        assert result.get("purchase_entry") is not None, "CSS求人は purchase_entry があるはず"
        assert result.get("sales_entry") is None, "CSS求人は sales_entry がないはず"

    def test_css_purchase_amount_is_tax_inclusive(self):
        """CSS求人の purchase_entry 金額は税抜×1.1（税込）"""
        record = _make_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = build_journal_entries(record)
        pe = result.get("purchase_entry")
        assert pe is not None
        amount = pe["details"][0]["amount"]
        assert amount == 55000, f"Expected 55000 (50000×1.1), got {amount}"

    def test_css_needs_invoice_is_false(self):
        """CSS求人は needs_invoice=False"""
        record = _make_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = build_journal_entries(record)
        assert result["needs_invoice"] is False

    def test_css_no_shukyaku_returns_register_scout_only_with_no_purchase(self):
        """CSS求人で集客手数料=0 の場合も register_scout_only（purchase_entry は None）"""
        record = _make_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=0.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "register_scout_only", (
            f"Expected register_scout_only, got {result['action']}"
        )
        assert result.get("purchase_entry") is None, (
            "集客手数料=0 なら purchase_entry は None のはず"
        )


# ============================================================
# テスト: 支払期日 due_date の代表ケース
# ============================================================

class TestDueDate:
    """calc_payment_date と build_journal_entries の due_date を検証する"""

    def test_calc_payment_date_翌々月10日(self):
        """入社翌々月10日: 2025-04-01 → 2025-06-10"""
        d = date(2025, 4, 1)
        result = calc_payment_date(d, "入社翌々月10日")
        assert result == date(2025, 6, 10), f"Expected 2025-06-10, got {result}"

    def test_calc_payment_date_翌々月4日(self):
        """入社翌々月4日: 2025-04-01 → 2025-06-04"""
        d = date(2025, 4, 1)
        result = calc_payment_date(d, "入社翌々月4日")
        assert result == date(2025, 6, 4), f"Expected 2025-06-04, got {result}"

    def test_calc_payment_date_翌月末(self):
        """入社翌月末: 2025-04-01 → 2025-05-31"""
        d = date(2025, 4, 1)
        result = calc_payment_date(d, "入社翌月末")
        assert result == date(2025, 5, 31), f"Expected 2025-05-31, got {result}"

    def test_calc_payment_date_翌々月末日(self):
        """入社翌々月末日: 2025-04-01 → 2025-06-30"""
        d = date(2025, 4, 1)
        result = calc_payment_date(d, "入社翌々月末日")
        assert result == date(2025, 6, 30), f"Expected 2025-06-30, got {result}"

    def test_calc_payment_date_登録不要_returns_none(self):
        """登録不要 → None"""
        d = date(2025, 4, 1)
        result = calc_payment_date(d, "登録不要")
        assert result is None

    def test_purchase_due_date_in_journal_circus(self):
        """Circus (入社翌々月10日) の purchase_entry.due_date が正しい"""
        record = _make_record("本部確認済", job_db="Circus", nyusha_date="2025-04-01")
        result = build_journal_entries(record)
        pe = result.get("purchase_entry")
        assert pe is not None, "purchase_entry が None"
        assert pe["due_date"] == "2025-06-10", (
            f"Circus due_date Expected 2025-06-10, got {pe['due_date']}"
        )

    def test_purchase_due_date_in_journal_zcareer(self):
        """Zキャリア (入社翌々月10日) の purchase_entry.due_date が正しい"""
        record = _make_record("本部確認済", job_db="Zキャリア", nyusha_date="2025-06-15")
        result = build_journal_entries(record)
        pe = result.get("purchase_entry")
        assert pe is not None, "purchase_entry が None"
        # 2025-06-15 → 翌々月 = 2025-08-10
        assert pe["due_date"] == "2025-08-10", (
            f"Zキャリア due_date Expected 2025-08-10, got {pe['due_date']}"
        )

    def test_purchase_due_date_in_journal_minabi(self):
        """マイナビJOBシェアリング (入社翌月末) の purchase_entry.due_date が正しい"""
        record = _make_record(
            "本部確認済",
            job_db="マイナビJOBシェアリング",
            nyusha_date="2025-04-01",
        )
        result = build_journal_entries(record)
        pe = result.get("purchase_entry")
        assert pe is not None, "purchase_entry が None"
        assert pe["due_date"] == "2025-05-31", (
            f"マイナビ due_date Expected 2025-05-31, got {pe['due_date']}"
        )


# ============================================================
# テスト: 返金短期離職の返金金額計算
# ============================================================

class TestRefundAmountCalc:
    """返金率に基づく返金金額計算を検証する"""

    def _make_refund_record(
        self,
        uriage: float,
        shukyaku: float,
        henkin_ritsu: float,
        taishoku_date: str = "2025-05-01",
    ) -> dict:
        props = {
            "名前": {"title": [{"plain_text": "テスト太郎"}]},
            "請求ステータス": {"type": "select", "select": {"name": "●返金（短期離職）"}},
            "入社日": {"type": "date", "date": {"start": "2025-04-01"}},
            "退職日•辞退日": {"type": "date", "date": {"start": taishoku_date}},
            "求人データベース": {"type": "select", "select": {"name": "Circus"}},
            "税抜売上": {"type": "number", "number": uriage},
            "税抜集客手数料": {"type": "number", "number": shukyaku},
            "返金料率": {"type": "number", "number": henkin_ritsu},
            "freee売上取引ID": {"type": "number", "number": None},
            "freee仕入取引ID": {"type": "number", "number": None},
            "freee請求書ID": {"type": "number", "number": None},
        }
        return {"id": "page-refund-calc", "_db_type": "honten", "properties": props}

    def test_refund_50pct_sales_amount(self):
        """返金率50%: 税抜売上500,000 → マイナス税込 -275,000"""
        record = self._make_refund_record(500000.0, 100000.0, 50.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        se = result.get("sales_entry")
        assert se is not None, "sales_entry が None"
        amount = se["details"][0]["amount"]
        # -500000 * 50/100 = -250000, 税込 = -250000 * 1.1 = -275000
        assert amount == -275000, f"Expected -275000, got {amount}"

    def test_refund_50pct_purchase_amount(self):
        """返金率50%: 税抜集客100,000 → マイナス税込 -55,000"""
        record = self._make_refund_record(500000.0, 100000.0, 50.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        pe = result.get("purchase_entry")
        assert pe is not None, "purchase_entry が None"
        amount = pe["details"][0]["amount"]
        # -100000 * 50/100 = -50000, 税込 = -50000 * 1.1 = -55000
        assert amount == -55000, f"Expected -55000, got {amount}"

    def test_refund_100pct_sales_amount(self):
        """返金率100%: 税抜売上500,000 → マイナス税込 -550,000"""
        record = self._make_refund_record(500000.0, 100000.0, 100.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        se = result.get("sales_entry")
        assert se is not None
        amount = se["details"][0]["amount"]
        assert amount == -550000, f"Expected -550000, got {amount}"

    def test_refund_30pct_sales_amount(self):
        """返金率30%: 税抜売上500,000 → マイナス税込 -165,000"""
        record = self._make_refund_record(500000.0, 100000.0, 30.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        se = result.get("sales_entry")
        assert se is not None
        amount = se["details"][0]["amount"]
        # -500000 * 30/100 = -150000, 税込 = -150000 * 1.1 = -165000
        assert amount == -165000, f"Expected -165000, got {amount}"

    def test_refund_zero_rate_without_override_returns_review(self):
        """返金率0% かつ返金後金額未設定 → review"""
        record = self._make_refund_record(500000.0, 100000.0, 0.0)
        result = build_journal_entries(record)
        assert result["action"] == "review", (
            f"返金率0%は review のはず: got {result['action']}"
        )

    def test_refund_due_date_is_next_month_end_of_taishoku(self):
        """売上返金の due_date は退職日の翌月末"""
        # 退職日 2025-05-01 → 翌月末 = 2025-06-30
        record = self._make_refund_record(500000.0, 100000.0, 50.0, taishoku_date="2025-05-01")
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        se = result.get("sales_entry")
        assert se is not None
        assert se["due_date"] == "2025-06-30", (
            f"退職日翌月末 Expected 2025-06-30, got {se['due_date']}"
        )
