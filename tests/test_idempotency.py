"""
idempotency テスト

- 6ルート（register / refund / delete / send_invoice / needs_invoice / register_scout_only）
  それぞれで freee成功後・Notion書き戻し失敗でも idempotency_keys に done + freee_ids が残ること
- processing stuck 防止テスト
- dry_run テスト
"""
import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# ヘルパー：テスト用インメモリ DB を processor._get_db に注入する
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """テスト用 SQLite DB を作成して接続を返す。"""
    db_path = str(tmp_path / "test.db")
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


def _get_idem_row(conn, key):
    """idempotency_keys から1行取得。"""
    return conn.execute(
        "SELECT * FROM idempotency_keys WHERE key=?", (key,)
    ).fetchone()


# ---------------------------------------------------------------------------
# 最小限の Notion レコード（各ルートで共通）
# ---------------------------------------------------------------------------

def _make_record(action: str, db_type: str = "honten") -> dict:
    """テスト用 Notion レコードを返す。"""
    base = {
        "id": "page-001",
        "_db_type": db_type,
        "properties": {
            "名前": {"title": [{"plain_text": "テスト太郎"}]},
            "ステータス": {"select": {"name": "本部確認済"}},
            "入社日": {"date": {"start": "2025-04-01"}},
            "請求有無": {"select": {"name": "請求あり"}},
            "求人DB": {"select": {"name": "本部"}},
            "freee取引ID（売上）": {"rich_text": []},
            "freee取引ID（仕入）": {"rich_text": []},
            "freee請求書ID": {"rich_text": []},
        },
    }
    return base


# ---------------------------------------------------------------------------
# 共通パッチ設定
# ---------------------------------------------------------------------------

COMMON_PATCHES = [
    "processor.set_invoice_required_select",
    "processor.clear_error_set_processing",
    "processor.mark_as_error",
    "processor.get_master_cache",
]


def _patch_common(monkeypatch):
    """外部 API 呼び出しを全て無効化する共通パッチ。"""
    for target in COMMON_PATCHES:
        monkeypatch.setattr(target, MagicMock(return_value=None))


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------

class TestIdempotencyRegister:
    """register ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback_on_success(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)

        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        # freee 登録は成功
        mock_reg = MagicMock(return_value={
            "sales_id": 1001,
            "purchase_id": 2001,
            "pca_id": None,
            "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)

        # Notion 書き戻しは失敗
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        result = processor.process_record(_make_record("register"))

        # freee は成功しているので idempotency_keys に done が記録されているはず
        row = _get_idem_row(conn, result.get("_idem_key") or _find_idem_key(conn, "page-001", "register"))
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done", f"Expected done, got {row['status']}"
        freee_ids = json.loads(row["freee_ids"])
        assert freee_ids.get("sales_id") == 1001
        assert freee_ids.get("purchase_id") == 2001
        conn.close()


class TestIdempotencyRefund:
    """refund ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "refund",
            "message": "返金",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": -500000},
            "purchase_entry": {"amount": -100000},
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        mock_reg = MagicMock(return_value={
            "sales_id": 1002,
            "purchase_id": 2002,
            "pca_id": None,
            "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        processor.process_record(_make_record("refund"))

        row = _find_idem_row(conn, "page-001", "refund")
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done"
        freee_ids = json.loads(row["freee_ids"])
        assert freee_ids.get("sales_id") == 1002
        conn.close()


class TestIdempotencyDelete:
    """delete ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "delete",
            "message": "入社前辞退",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "delete_sales_id": 1003,
            "delete_purchase_id": 2003,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        mock_del = MagicMock(return_value={"errors": []})
        monkeypatch.setattr("processor.delete_deals", mock_del)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        processor.process_record(_make_record("delete"))

        row = _find_idem_row(conn, "page-001", "delete")
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done"
        conn.close()


class TestIdempotencySendInvoice:
    """send_invoice ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "send_invoice",
            "message": "請求書送付",
            "original_status": "入社済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        monkeypatch.setattr("processor.get_invoice_id_from_record", MagicMock(return_value=9001))
        mock_send = MagicMock(return_value={"error": None})
        monkeypatch.setattr("processor.send_invoice", mock_send)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        processor.process_record(_make_record("send_invoice"))

        row = _find_idem_row(conn, "page-001", "send_invoice")
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done"
        freee_ids = json.loads(row["freee_ids"])
        assert freee_ids.get("invoice_id") == 9001
        conn.close()


class TestIdempotencyNeedsInvoice:
    """needs_invoice ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        # needs_invoice ルートは journal["action"] == "register" かつ needs_invoice=True で分岐する
        journal = {
            "action": "register",
            "needs_invoice": True,
            "message": "請求書登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000, "issue_date": "2025-04-01"},
            "purchase_entry": None,
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())
        # _extract_props と _build_invoice_entry は rules モジュールから直接インポートされているため
        # processor モジュール内の参照をパッチする
        monkeypatch.setattr("processor._extract_props", MagicMock(return_value={}))
        monkeypatch.setattr("processor._build_invoice_entry", MagicMock(return_value={}))

        mock_inv = MagicMock(return_value={
            "invoice_id": 8001,
            "errors": [],
        })
        # register_invoice_and_deal は freee_client からインポートされているため
        # processor モジュール内の参照をパッチする
        monkeypatch.setattr("processor.register_invoice_and_deal", mock_inv)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        processor.process_record(_make_record("needs_invoice"))

        # needs_invoice ルートは journal["action"]="register" で _idem_start するため
        # DB の action カラムは "register" のまま。key で直接検索する。
        idem_key = processor._idem_key("page-001", "register", journal)
        row = conn.execute("SELECT * FROM idempotency_keys WHERE key=?", (idem_key,)).fetchone()
        assert row is not None, f"idempotency_keys にレコードが存在しない（key={idem_key}）"
        assert row["status"] == "done", f"Expected done, got {row['status']}"
        freee_ids = json.loads(row["freee_ids"])
        assert freee_ids.get("invoice_id") == 8001
        conn.close()


class TestIdempotencyRegisterScoutOnly:
    """register_scout_only ルート: freee成功・Notion書き戻し失敗でも done が残る"""

    def test_idem_saved_before_notion_writeback(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register_scout_only",
            "message": "スカウト手数料登録",
            "original_status": "本部確認済",
            "job_db": "CSS",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "purchase_entry": {"amount": 50000},
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())
        monkeypatch.setattr("processor.get_master_cache", MagicMock(return_value={}))

        mock_deal = MagicMock(return_value={"id": 3001})
        monkeypatch.setattr("processor.create_deal", mock_deal)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=False))

        import processor
        processor.process_record(_make_record("register_scout_only"))

        row = _find_idem_row(conn, "page-001", "register_scout_only")
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done"
        freee_ids = json.loads(row["freee_ids"])
        assert freee_ids.get("purchase_id") == 3001
        conn.close()


# ---------------------------------------------------------------------------
# processing stuck 防止テスト
# ---------------------------------------------------------------------------

class TestProcessingStuck:
    """freee処理失敗時、idempotency_keys に processing のまま残らないこと"""

    def test_freee_error_sets_status_to_error(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        # freee 登録は失敗
        mock_reg = MagicMock(return_value={
            "sales_id": None,
            "purchase_id": None,
            "pca_id": None,
            "errors": ["freee API error"],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)

        import processor
        processor.process_record(_make_record("register"))

        row = _find_idem_row(conn, "page-001", "register")
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "error", f"Expected error, got {row['status']}"
        conn.close()

    def test_exception_sets_status_to_error(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        # freee 登録で例外が発生
        monkeypatch.setattr("processor.register_journal", MagicMock(side_effect=RuntimeError("unexpected")))

        import processor
        result = processor.process_record(_make_record("register"))

        assert result["status"] == "error"
        row = _find_idem_row(conn, "page-001", "register")
        # _idem_start は register_journal 呼び出し前に実行されるため、
        # 例外発生後も DB に行が存在するはず
        assert row is not None, "idempotency_keys にレコードが存在しない（_idem_start が呼ばれていない可能性）"
        assert row["status"] == "error", f"Expected error, got {row['status']}"
        conn.close()

    def test_stale_processing_is_error_and_retried(self, tmp_path, monkeypatch):
        """30分以上古い processing は stale として error 化され、再実行される"""
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }

        # 31分前の processing レコードを挿入
        # processor.py の stale 判定は UTC 基準（datetime('now') = UTC）なので UTC で作成する
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
        import processor
        idem_key = processor._idem_key("page-001", "register", journal)
        conn.execute(
            """INSERT INTO idempotency_keys (key, page_id, action, status, freee_ids, created_at, updated_at)
               VALUES (?, ?, ?, 'processing', '{}', ?, ?)""",
            (idem_key, "page-001", "register", stale_time, stale_time)
        )
        conn.commit()

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        mock_reg = MagicMock(return_value={
            "sales_id": 1001,
            "purchase_id": 2001,
            "pca_id": None,
            "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=True))

        result = processor.process_record(_make_record("register"))

        # stale なので再実行され、freee 登録が成功するはず
        assert result["status"] in ("success", "partial_error"), (
            f"stale processing は再実行され success / partial_error になるはず: got {result['status']}"
        )
        # freee 登録が実際に再実行されたことを確認
        mock_reg.assert_called_once(), "stale processing 後に register_journal が呼ばれていない"
        # DB 上でも processing のまま残っていないことを確認
        row = conn.execute(
            "SELECT status FROM idempotency_keys WHERE key=?", (idem_key,)
        ).fetchone()
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] != "processing", (
            f"stale processing が processing のまま残っている: {row['status']}"
        )
        conn.close()


# ---------------------------------------------------------------------------
# done 済み idempotency key dedup テスト
# ---------------------------------------------------------------------------

class TestIdempotencyDedup:
    """done 済みの idempotency key がある場合、freee 登録が再実行されないこと"""

    def test_done_key_skips_freee_registration(self, tmp_path, monkeypatch):
        """done 済みキーがある場合は skip を返し、freee 登録・削除・請求書送付が呼ばれない"""
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"issue_date": "2025-04-01", "amount": 500000},
            "purchase_entry": {"issue_date": "2025-04-01", "amount": 100000},
            "pca_entry": None,
        }

        # _idem_key と同じロジックで事前にキーを計算して done 行を挿入
        import processor
        idem_key = processor._idem_key("page-001", "register", journal)
        saved_freee_ids = json.dumps({"sales_id": 9999, "purchase_id": 8888})
        conn.execute(
            """INSERT INTO idempotency_keys
               (key, page_id, action, status, freee_ids, created_at, updated_at)
               VALUES (?, ?, ?, 'done', ?, datetime('now'), datetime('now'))""",
            (idem_key, "page-001", "register", saved_freee_ids)
        )
        conn.commit()

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())

        mock_reg = MagicMock()
        monkeypatch.setattr("processor.register_journal", mock_reg)
        mock_delete = MagicMock()
        monkeypatch.setattr("processor.delete_deals", mock_delete)
        mock_send = MagicMock()
        monkeypatch.setattr("processor.send_invoice", mock_send)
        mock_done = MagicMock()
        monkeypatch.setattr("processor.mark_as_done", mock_done)

        result = processor.process_record(_make_record("register"))

        # done 済みなので skip を返すこと
        assert result["status"] == "skip", (
            f"done 済みキーがある場合は skip を返すはず: got {result['status']}"
        )
        # freee 登録・削除・請求書送付・Notion 書き戻しが呼ばれないこと
        mock_reg.assert_not_called()
        mock_delete.assert_not_called()
        mock_send.assert_not_called()
        mock_done.assert_not_called()
        # DB の freee_ids が保持されていること
        row = conn.execute(
            "SELECT status, freee_ids FROM idempotency_keys WHERE key=?", (idem_key,)
        ).fetchone()
        assert row is not None, "idempotency_keys にレコードが存在しない"
        assert row["status"] == "done", f"done のまま保持されるはず: got {row['status']}"
        stored = json.loads(row["freee_ids"])
        assert stored["sales_id"] == 9999, f"freee_ids が上書きされた: {stored}"
        assert stored["purchase_id"] == 8888, f"freee_ids が上書きされた: {stored}"
        conn.close()


# ---------------------------------------------------------------------------
# dry_run テスト
# ---------------------------------------------------------------------------

class TestDryRun:
    """dry_run=True の場合、freee登録・削除・請求書送付・Notion書き戻しが呼ばれない"""

    def test_dry_run_does_not_call_freee_or_notion(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())

        mock_reg = MagicMock()
        monkeypatch.setattr("processor.register_journal", mock_reg)
        mock_done = MagicMock()
        monkeypatch.setattr("processor.mark_as_done", mock_done)
        mock_err = MagicMock()
        monkeypatch.setattr("processor.mark_as_error", mock_err)
        mock_del = MagicMock()
        monkeypatch.setattr("processor.delete_deals", mock_del)
        mock_send = MagicMock()
        monkeypatch.setattr("processor.send_invoice", mock_send)

        import processor
        result = processor.process_record(_make_record("register"), dry_run=True)

        assert result["status"] == "dry_run"
        mock_reg.assert_not_called()
        mock_done.assert_not_called()
        mock_del.assert_not_called()
        mock_send.assert_not_called()
        conn.close()

    def test_dry_run_does_not_call_freee_for_delete(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "delete",
            "message": "入社前辞退",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "delete_sales_id": 1003,
            "delete_purchase_id": 2003,
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())

        mock_del = MagicMock()
        monkeypatch.setattr("processor.delete_deals", mock_del)
        mock_done = MagicMock()
        monkeypatch.setattr("processor.mark_as_done", mock_done)

        import processor
        result = processor.process_record(_make_record("delete"), dry_run=True)

        assert result["status"] == "dry_run"
        mock_del.assert_not_called()
        mock_done.assert_not_called()
        conn.close()

    def test_dry_run_does_not_call_send_invoice(self, tmp_path, monkeypatch):
        db_path, conn = _make_db(tmp_path)
        import db as db_module
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        journal = {
            "action": "send_invoice",
            "message": "請求書送付",
            "original_status": "入社済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
        }

        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())

        mock_send = MagicMock()
        monkeypatch.setattr("processor.send_invoice", mock_send)
        mock_done = MagicMock()
        monkeypatch.setattr("processor.mark_as_done", mock_done)

        import processor
        result = processor.process_record(_make_record("send_invoice"), dry_run=True)

        assert result["status"] == "dry_run"
        mock_send.assert_not_called()
        mock_done.assert_not_called()
        conn.close()


# ---------------------------------------------------------------------------
# ユーティリティ関数
# ---------------------------------------------------------------------------

def _find_idem_row(conn, page_id: str, action: str):
    """page_id と action で idempotency_keys を検索する。"""
    return conn.execute(
        "SELECT * FROM idempotency_keys WHERE page_id=? AND action=?",
        (page_id, action)
    ).fetchone()


def _find_idem_key(conn, page_id: str, action: str):
    """page_id と action で idempotency_keys の key を取得する。"""
    row = conn.execute(
        "SELECT key FROM idempotency_keys WHERE page_id=? AND action=?",
        (page_id, action)
    ).fetchone()
    return row["key"] if row else None
