"""
tests/test_decimal_tax.py

rules.py の消費税計算 Decimal 化に対するテスト。

テスト対象:
- _to_decimal / _int_trunc_decimal / _tax_included_10 / _percent_amount ヘルパー単体
- 端数処理（333,333 円など float 誤差が出やすいケース）
- PCA 返金計算（_percent_amount 経由）
- build_journal_entries 経由で実際の計算経路を通すテスト

本番 freee / Notion / OpenAI は一切叩かない。
"""
import sys
import os
from decimal import Decimal
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rules import (
    _to_decimal,
    _int_trunc_decimal,
    _tax_included_10,
    _percent_amount,
    build_journal_entries,
)


# ============================================================
# autouse fixture: 外部 API 呼び出しをモック
# ============================================================
@pytest.fixture(autouse=True)
def mock_notion_name_lookups(monkeypatch):
    import notion_client as nc
    monkeypatch.setattr(nc, "get_jobseeker_name", lambda record: "テスト太郎")
    monkeypatch.setattr(nc, "get_company_name", lambda record: "株式会社テスト")


# ============================================================
# ヘルパー単体テスト
# ============================================================
class TestToDecimal:
    def test_int_input(self):
        assert _to_decimal(500000) == Decimal("500000")

    def test_float_input(self):
        assert _to_decimal(500000.0) == Decimal("500000.0")

    def test_none_returns_zero(self):
        assert _to_decimal(None) == Decimal("0")

    def test_string_input(self):
        assert _to_decimal("333333") == Decimal("333333")


class TestIntTruncDecimal:
    def test_positive_truncates_toward_zero(self):
        # 1.9 → 1（切り捨て）
        assert _int_trunc_decimal(Decimal("1.9")) == 1

    def test_negative_truncates_toward_zero(self):
        # -1.9 → -1（0方向への切り捨て）
        assert _int_trunc_decimal(Decimal("-1.9")) == -1

    def test_exact_integer(self):
        assert _int_trunc_decimal(Decimal("275000")) == 275000


class TestTaxIncluded10:
    def test_standard_case(self):
        # 250000 * 1.1 = 275000
        assert _tax_included_10(250000) == 275000

    def test_333333_no_float_error(self):
        # 333333 * 1.1 = 366666.3 → 366666（切り捨て）
        # float では 333333 * 1.1 = 366666.30000000003 になる可能性
        result = _tax_included_10(333333)
        assert result == 366666, f"Expected 366666, got {result}"

    def test_negative_amount(self):
        # -250000 * 1.1 = -275000（0方向切り捨て）
        assert _tax_included_10(-250000) == -275000

    def test_negative_333333(self):
        # -333333 * 1.1 = -366666.3 → -366666（0方向切り捨て）
        result = _tax_included_10(-333333)
        assert result == -366666, f"Expected -366666, got {result}"

    def test_zero(self):
        assert _tax_included_10(0) == 0

    def test_none_input(self):
        # None は 0 として扱う
        assert _tax_included_10(None) == 0


class TestPercentAmount:
    def test_50pct_of_500000(self):
        # 500000 * 50 / 100 = 250000
        assert _percent_amount(500000, 50) == 250000

    def test_30pct_of_500000(self):
        # 500000 * 30 / 100 = 150000
        assert _percent_amount(500000, 30) == 150000

    def test_100pct_of_500000(self):
        assert _percent_amount(500000, 100) == 500000

    def test_33pct_of_333333_no_float_error(self):
        # 333333 * 33 / 100 = 109999.89 → 109999（切り捨て）
        result = _percent_amount(333333, 33)
        assert result == 109999, f"Expected 109999, got {result}"

    def test_zero_rate(self):
        assert _percent_amount(500000, 0) == 0

    def test_zero_amount(self):
        assert _percent_amount(0, 50) == 0


# ============================================================
# build_journal_entries 経由のテスト（本体経路を通す）
# ============================================================
def _make_refund_record_pca(
    uriage: float,
    shukyaku: float,
    pca_shiire: float,
    henkin_ritsu: float,
) -> dict:
    """
    PCA 成約管理の返金レコードを組み立てる。
    db_type="pca" の場合:
      - pca_shiire は「受取報酬料（税込）」フィールドから取得される
      - 求職者名は「求職者」title 型
      - 入社企業名は「決定企業」rich_text 型
    """
    props = {
        "名前": {"title": [{"plain_text": "テスト太郎"}]},
        "フェーズ": {"title": [{"plain_text": ""}]},
        "請求ステータス": {"type": "select", "select": {"name": "●返金（短期離職）"}},
        "求人データベース": {"type": "select", "select": {"name": "PCA"}},
        "集客経路": {"type": "select", "select": {"name": "PCA"}},
        "入社日": {"type": "date", "date": {"start": "2025-04-01"}},
        "退職日•辞退日": {"type": "date", "date": {"start": "2025-05-01"}},
        "税抜売上": {"type": "number", "number": uriage},
        "税抜集客手数料": {"type": "number", "number": shukyaku},
        # db_type="pca" では「受取報酬料（税込）」が pca_shiire として使われる
        "受取報酬料（税込）": {"type": "number", "number": pca_shiire},
        "返金料率": {"type": "number", "number": henkin_ritsu},
        "freee売上取引ID": {"type": "number", "number": None},
        "freee仕入取引ID": {"type": "number", "number": None},
        "freee仕入取引ID（PCA）": {"type": "number", "number": None},
        # PCA 専用: 求職者は title 型
        "求職者": {"title": [{"plain_text": "テスト太郎"}]},
        # PCA 専用: 決定企業は rich_text 型
        "決定企業": {"rich_text": [{"plain_text": "株式会社テスト"}]},
    }
    return {"id": "page-pca-refund", "_db_type": "pca", "properties": props}


class TestPcaRefundAmountCalc:
    """PCA 返金計算（_percent_amount 経由）のテスト"""

    def test_pca_refund_50pct(self):
        """PCA仕入500,000 × 50% → pca_entry amount = -250,000"""
        record = _make_refund_record_pca(
            uriage=500000.0,
            shukyaku=100000.0,
            pca_shiire=500000.0,
            henkin_ritsu=50.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        pca = result.get("pca_entry")
        assert pca is not None, "pca_entry が None"
        amount = pca["details"][0]["amount"]
        # -500000 * 50 / 100 = -250000
        assert amount == -250000, f"Expected -250000, got {amount}"

    def test_pca_refund_30pct(self):
        """PCA仕入333,333 × 30% → pca_entry amount = -99,999（切り捨て）"""
        record = _make_refund_record_pca(
            uriage=500000.0,
            shukyaku=100000.0,
            pca_shiire=333333.0,
            henkin_ritsu=30.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        pca = result.get("pca_entry")
        assert pca is not None, "pca_entry が None"
        amount = pca["details"][0]["amount"]
        # -333333 * 30 / 100 = -99999.9 → -99999（0方向切り捨て）
        assert amount == -99999, f"Expected -99999, got {amount}"

    def test_pca_refund_100pct(self):
        """PCA仕入500,000 × 100% → pca_entry amount = -500,000"""
        record = _make_refund_record_pca(
            uriage=500000.0,
            shukyaku=100000.0,
            pca_shiire=500000.0,
            henkin_ritsu=100.0,
        )
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        pca = result.get("pca_entry")
        assert pca is not None
        amount = pca["details"][0]["amount"]
        assert amount == -500000, f"Expected -500000, got {amount}"


class TestRefundAmountDecimalEdgeCases:
    """返金計算の端数ケース（float 誤差が出やすい値）"""

    def _make_refund_record(
        self,
        uriage: float,
        shukyaku: float,
        henkin_ritsu: float,
    ) -> dict:
        props = {
            "名前": {"title": [{"plain_text": "テスト太郎"}]},
            "請求ステータス": {"type": "select", "select": {"name": "●返金（短期離職）"}},
            "入社日": {"type": "date", "date": {"start": "2025-04-01"}},
            "退職日•辞退日": {"type": "date", "date": {"start": "2025-05-01"}},
            "求人データベース": {"type": "select", "select": {"name": "Circus"}},
            "税抜売上": {"type": "number", "number": uriage},
            "税抜集客手数料": {"type": "number", "number": shukyaku},
            "返金料率": {"type": "number", "number": henkin_ritsu},
            "freee売上取引ID": {"type": "number", "number": None},
            "freee仕入取引ID": {"type": "number", "number": None},
            "freee請求書ID": {"type": "number", "number": None},
        }
        return {"id": "page-edge", "_db_type": "honten", "properties": props}

    def test_333333_sales_50pct_refund(self):
        """税抜売上333,333 × 50% → -166,666 → 税込 -183,332（切り捨て）"""
        record = self._make_refund_record(333333.0, 100000.0, 50.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        se = result.get("sales_entry")
        assert se is not None
        amount = se["details"][0]["amount"]
        # 333333 * 50/100 = 166666.5 → 166666（切り捨て）
        # 166666 * 1.1 = 183332.6 → 183332（切り捨て）
        # マイナスなので -183332
        assert amount == -183332, f"Expected -183332, got {amount}"

    def test_333333_purchase_50pct_refund(self):
        """税抜集客333,333 × 50% → -166,666 → 税込 -183,332（切り捨て）"""
        record = self._make_refund_record(500000.0, 333333.0, 50.0)
        result = build_journal_entries(record)
        assert result["action"] == "refund"
        pe = result.get("purchase_entry")
        assert pe is not None
        amount = pe["details"][0]["amount"]
        # 333333 * 50/100 = 166666.5 → 166666（切り捨て）
        # 166666 * 1.1 = 183332.6 → 183332（切り捨て）
        # マイナスなので -183332
        assert amount == -183332, f"Expected -183332, got {amount}"
