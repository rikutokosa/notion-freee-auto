"""
tests/test_integration.py
processor × rules 継ぎ目テスト

実 Notion 風レコードを build_journal_entries に通し、
その結果を process_record に渡して、
action が想定ルートに流れることを確認する。

freee / Notion / OpenAI / Slack など外部 API はすべて mock。
本番 API は絶対に叩かない。
"""
import sqlite3
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# ヘルパー: テスト用インメモリ DB
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """テスト用 SQLite DB を作成して接続を返す。"""
    db_path = str(tmp_path / "test_integration.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            freee_ids TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return db_path, conn


def _find_idem_row(conn, page_id, action):
    return conn.execute(
        "SELECT * FROM idempotency_keys WHERE page_id=? AND action=?",
        (page_id, action),
    ).fetchone()


# ---------------------------------------------------------------------------
# ヘルパー: 実 Notion 風レコードを組み立てる
# ---------------------------------------------------------------------------

def _make_notion_record(
    status: str,
    job_db: str = "Circus",
    nyusha_date: str = "2025-04-01",
    uriage: float = 500000.0,
    shukyaku: float = 100000.0,
    freee_sales_id: float | None = None,
    freee_purchase_id: float | None = None,
    freee_invoice_id: float | None = None,
    invoice_required: str | None = None,
    db_type: str = "honten",
    henkin_ritsu: float | None = None,
    taishoku_date: str | None = None,
    page_id: str = "page-integ-001",
) -> dict:
    """
    実際の Notion API レスポンスに近い形式でレコードを生成する。
    _extract_props が読むプロパティキー名・型に合わせる。
    """
    props: dict = {
        "名前": {"title": [{"plain_text": "テスト太郎"}]},
        "フェーズ": {"title": [{"plain_text": "テストフェーズ"}]},
        "請求ステータス": {"type": "select", "select": {"name": status}},
        "入社日": {"type": "date", "date": {"start": nyusha_date}},
        "求人データベース": {"type": "select", "select": {"name": job_db}},
        "税抜売上": {"type": "number", "number": uriage},
        "税抜集客手数料": {"type": "number", "number": shukyaku},
        "freee売上取引ID": {"type": "number", "number": freee_sales_id},
        "freee仕入取引ID": {"type": "number", "number": freee_purchase_id},
        "freee請求書ID": {"type": "number", "number": freee_invoice_id},
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
        "id": page_id,
        "_db_type": db_type,
        "properties": props,
    }


# ---------------------------------------------------------------------------
# 共通パッチ: 外部 API をすべて mock
# ---------------------------------------------------------------------------

def _patch_all_external(monkeypatch, tmp_path):
    """
    processor が呼ぶ外部 API をすべて mock する。
    DB は tmp_path の SQLite に差し替える。
    """
    import db as db_module
    db_path, conn = _make_db(tmp_path)
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)

    # Notion 関連
    monkeypatch.setattr("processor.set_invoice_required_select", MagicMock(return_value=None))
    monkeypatch.setattr("processor.clear_error_set_processing", MagicMock(return_value=None))
    monkeypatch.setattr("processor.clear_error", MagicMock(return_value=None))
    monkeypatch.setattr("processor.mark_as_error", MagicMock(return_value=None))
    monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=True))

    # notion_client の名前解決 mock（rules.py 経由）
    import notion_client as nc
    monkeypatch.setattr(nc, "get_jobseeker_name", lambda r: "テスト太郎")
    monkeypatch.setattr(nc, "get_company_name", lambda r: "株式会社テスト")

    return db_path, conn


# ---------------------------------------------------------------------------
# テスト: register ルート（Circus / 仕訳のみ）
# ---------------------------------------------------------------------------

class TestIntegrationRegister:
    """
    実 Notion 風レコード → build_journal_entries → process_record
    register ルートが正しく流れることを確認する
    """

    def test_register_route_dry_run(self, tmp_path, monkeypatch):
        """
        Circus + 本部確認済 → dry_run=True で action=register・status=dry_run
        外部 API は一切呼ばれない
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_register = MagicMock()
        monkeypatch.setattr("processor.register_journal", mock_register)

        import processor
        record = _make_notion_record("本部確認済", job_db="Circus")
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "register", (
            f"Expected register, got {result['action']}: {result.get('message')}"
        )
        assert result["status"] == "dry_run", (
            f"Expected dry_run, got {result['status']}"
        )
        mock_register.assert_not_called()
        conn.close()

    def test_register_route_calls_register_journal(self, tmp_path, monkeypatch):
        """
        Circus + 本部確認済 → process_record が register_journal を呼ぶ
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_register = MagicMock(return_value={
            "sales_id": 1001,
            "purchase_id": 2001,
            "pca_id": None,
            "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_register)

        import processor
        record = _make_notion_record("本部確認済", job_db="Circus")
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "register"
        assert result["status"] == "success"
        mock_register.assert_called_once()
        conn.close()

    def test_register_journal_receives_correct_amounts(self, tmp_path, monkeypatch):
        """
        build_journal_entries の sales_entry / purchase_entry が
        register_journal に正しく渡される（税込金額）
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        captured = {}

        def mock_register(sales_entry, purchase_entry, pca_entry):
            captured["sales_entry"] = sales_entry
            captured["purchase_entry"] = purchase_entry
            return {"sales_id": 1001, "purchase_id": 2001, "pca_id": None, "errors": []}

        monkeypatch.setattr("processor.register_journal", mock_register)

        import processor
        # 税抜売上 500000 → 税込 550000
        # 税抜集客 100000 → 税込 110000
        record = _make_notion_record(
            "本部確認済", job_db="Circus",
            uriage=500000.0, shukyaku=100000.0,
        )
        processor.process_record(record, dry_run=False)

        assert captured.get("sales_entry") is not None
        assert captured["sales_entry"]["details"][0]["amount"] == 550000
        assert captured.get("purchase_entry") is not None
        assert captured["purchase_entry"]["details"][0]["amount"] == 110000
        conn.close()


# ---------------------------------------------------------------------------
# テスト: send_invoice ルート（●入社済 + 要請求）
# ---------------------------------------------------------------------------

class TestIntegrationSendInvoice:
    """
    ●入社済 + 要請求 → send_invoice ルートが正しく流れることを確認する
    """

    def test_send_invoice_route_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → action=send_invoice・status=dry_run"""
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "●入社済",
            job_db="Circus",
            invoice_required="要請求",
        )
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "send_invoice", (
            f"Expected send_invoice, got {result['action']}: {result.get('message')}"
        )
        assert result["status"] == "dry_run"
        conn.close()

    def test_send_invoice_calls_send_invoice_api(self, tmp_path, monkeypatch):
        """
        ●入社済 + 要請求 + freee請求書ID あり → send_invoice が呼ばれる
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_send = MagicMock(return_value={"error": None})
        monkeypatch.setattr("processor.send_invoice", mock_send)

        import processor
        record = _make_notion_record(
            "●入社済",
            job_db="Circus",
            invoice_required="要請求",
            freee_invoice_id=9999.0,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "send_invoice"
        assert result["status"] == "success"
        mock_send.assert_called_once_with(9999)
        conn.close()

    def test_send_invoice_no_invoice_id_returns_error(self, tmp_path, monkeypatch):
        """
        ●入社済 + 要請求 + freee請求書ID なし → error
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "●入社済",
            job_db="Circus",
            invoice_required="要請求",
            freee_invoice_id=None,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "send_invoice"
        assert result["status"] == "error"
        assert "freee請求書ID" in result["message"]
        conn.close()


# ---------------------------------------------------------------------------
# テスト: register_scout_only ルート（CSS求人）
# ---------------------------------------------------------------------------

class TestIntegrationRegisterScoutOnly:
    """
    CSS求人 + 本部確認済 → register_scout_only ルートが正しく流れることを確認する
    """

    def test_register_scout_only_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → action=register_scout_only・status=dry_run"""
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "register_scout_only", (
            f"Expected register_scout_only, got {result['action']}: {result.get('message')}"
        )
        assert result["status"] == "dry_run"
        conn.close()

    def test_register_scout_only_calls_create_deal(self, tmp_path, monkeypatch):
        """
        CSS求人 + 本部確認済 → create_deal が呼ばれる（register_journal は呼ばれない）
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_create_deal = MagicMock(return_value={"id": 3001})
        mock_register_journal = MagicMock()
        monkeypatch.setattr("processor.create_deal", mock_create_deal)
        monkeypatch.setattr("processor.register_journal", mock_register_journal)
        monkeypatch.setattr("processor.get_master_cache", MagicMock(return_value={}))

        import processor
        record = _make_notion_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=50000.0,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "register_scout_only"
        assert result["status"] == "success"
        mock_create_deal.assert_called_once()
        mock_register_journal.assert_not_called()
        conn.close()

    def test_register_scout_only_no_shukyaku_skips_create_deal(self, tmp_path, monkeypatch):
        """
        CSS求人 + 集客手数料=0 → create_deal は呼ばれず success
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_create_deal = MagicMock(return_value={"id": 3001})
        monkeypatch.setattr("processor.create_deal", mock_create_deal)
        monkeypatch.setattr("processor.get_master_cache", MagicMock(return_value={}))

        import processor
        record = _make_notion_record(
            "本部確認済",
            job_db="CSS求人",
            uriage=0.0,
            shukyaku=0.0,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "register_scout_only"
        assert result["status"] == "success"
        mock_create_deal.assert_not_called()
        conn.close()


# ---------------------------------------------------------------------------
# テスト: refund ルート（●返金（短期離職））
# ---------------------------------------------------------------------------

class TestIntegrationRefund:
    """
    ●返金（短期離職） → refund ルートが正しく流れることを確認する
    """

    def test_refund_route_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → action=refund・status=dry_run"""
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "●返金（短期離職）",
            job_db="Circus",
            uriage=500000.0,
            shukyaku=100000.0,
            henkin_ritsu=50.0,
            taishoku_date="2025-05-01",
        )
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "refund", (
            f"Expected refund, got {result['action']}: {result.get('message')}"
        )
        assert result["status"] == "dry_run"
        conn.close()

    def test_refund_calls_register_journal_with_negative_amounts(self, tmp_path, monkeypatch):
        """
        返金ルート → register_journal がマイナス金額で呼ばれる
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        captured = {}

        def mock_register(sales_entry, purchase_entry, pca_entry):
            captured["sales_entry"] = sales_entry
            captured["purchase_entry"] = purchase_entry
            return {"sales_id": 1002, "purchase_id": 2002, "pca_id": None, "errors": []}

        monkeypatch.setattr("processor.register_journal", mock_register)

        import processor
        record = _make_notion_record(
            "●返金（短期離職）",
            job_db="Circus",
            uriage=500000.0,
            shukyaku=100000.0,
            henkin_ritsu=50.0,
            taishoku_date="2025-05-01",
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "refund"
        assert result["status"] == "success"
        # 返金率50%: 税抜売上500000 → -250000 → 税込 -275000
        se = captured.get("sales_entry")
        assert se is not None
        assert se["details"][0]["amount"] < 0, "返金の sales_entry はマイナス金額のはず"
        assert se["details"][0]["amount"] == -275000
        conn.close()


# ---------------------------------------------------------------------------
# テスト: delete ルート（●入社前辞退）
# ---------------------------------------------------------------------------

class TestIntegrationDelete:
    """
    ●入社前辞退 → delete ルートが正しく流れることを確認する
    """

    def test_delete_route_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → action=delete・status=dry_run"""
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "●入社前辞退",
            job_db="Circus",
            freee_sales_id=12345.0,
            freee_purchase_id=67890.0,
        )
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "delete", (
            f"Expected delete, got {result['action']}: {result.get('message')}"
        )
        assert result["status"] == "dry_run"
        conn.close()

    def test_delete_calls_delete_deals(self, tmp_path, monkeypatch):
        """
        ●入社前辞退 + freee ID あり → delete_deals が正しい ID で呼ばれる
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_delete = MagicMock(return_value={"errors": []})
        monkeypatch.setattr("processor.delete_deals", mock_delete)

        import processor
        record = _make_notion_record(
            "●入社前辞退",
            job_db="Circus",
            freee_sales_id=12345.0,
            freee_purchase_id=67890.0,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "delete"
        assert result["status"] == "success"
        mock_delete.assert_called_once_with(12345, 67890)
        conn.close()

    def test_delete_without_freee_id_returns_review(self, tmp_path, monkeypatch):
        """
        ●入社前辞退 + freee ID なし → review（delete_deals は呼ばれない）
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_delete = MagicMock()
        monkeypatch.setattr("processor.delete_deals", mock_delete)

        import processor
        record = _make_notion_record(
            "●入社前辞退",
            job_db="Circus",
            freee_sales_id=None,
            freee_purchase_id=None,
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "review", (
            f"freee ID なしは review のはず: got {result['action']}"
        )
        mock_delete.assert_not_called()
        conn.close()


# ---------------------------------------------------------------------------
# テスト: needs_invoice ルート（マイナビJOBシェアリング / 本部確認済）
# ---------------------------------------------------------------------------

class TestIntegrationNeedsInvoice:
    """
    needs_invoice=True → register_invoice_and_deal が呼ばれることを確認する
    """

    def test_needs_invoice_route_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → action=register・status=dry_run（needs_invoice=True）"""
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        import processor
        record = _make_notion_record(
            "本部確認済",
            job_db="マイナビJOBシェアリング",
        )
        result = processor.process_record(record, dry_run=True)

        assert result["action"] == "register"
        assert result["status"] == "dry_run"
        assert result["journal"]["needs_invoice"] is True
        conn.close()

    def test_needs_invoice_calls_register_invoice_and_deal(self, tmp_path, monkeypatch):
        """
        needs_invoice=True → register_invoice_and_deal が呼ばれる
        register_journal は呼ばれない
        """
        db_path, conn = _patch_all_external(monkeypatch, tmp_path)

        mock_inv = MagicMock(return_value={
            "invoice_id": 5001,
            "errors": [],
        })
        mock_register = MagicMock()
        monkeypatch.setattr("processor.register_invoice_and_deal", mock_inv)
        monkeypatch.setattr("processor.register_journal", mock_register)
        monkeypatch.setattr("processor.get_master_cache", MagicMock(return_value={}))
        monkeypatch.setattr("processor.create_deal", MagicMock(return_value={"id": 2001}))

        import processor
        record = _make_notion_record(
            "本部確認済",
            job_db="マイナビJOBシェアリング",
        )
        result = processor.process_record(record, dry_run=False)

        assert result["action"] == "register"
        assert result["status"] == "success"
        mock_inv.assert_called_once()
        mock_register.assert_not_called()
        conn.close()
