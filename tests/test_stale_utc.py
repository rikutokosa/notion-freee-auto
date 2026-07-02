"""
stale 判定 UTC 統一テスト

- _parse_idem_updated_at_as_utc のユニットテスト
- stale 判定が UTC 基準で動くことの統合テスト
- legacy JST localtime レコードが正しく吸収されることのテスト
- idempotency 書き込み経路に datetime('now','localtime') が残っていないことの確認
"""
import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# ヘルパー：テスト用 SQLite DB を作成して processor._get_db に注入する
# ---------------------------------------------------------------------------

def _make_idem_db(tmp_path):
    """テスト用 SQLite DB を作成して (db_path, conn) を返す。"""
    db_path = str(tmp_path / "test_stale.db")
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


def _make_record(action: str = "register") -> dict:
    return {
        "id": "page-stale-001",
        "properties": {
            "freee_status": {"select": {"name": "②経理対応待ち"}},
            "action": {"select": {"name": action}},
            "jobseeker_name": {"title": [{"text": {"content": "テスト太郎"}}]},
            "nyusha_date": {"date": {"start": "2025-04-01"}},
            "job_db": {"select": {"name": "本部"}},
            "amount": {"number": 500000},
        },
    }


# ---------------------------------------------------------------------------
# TestParseIdemUpdatedAtAsUtc: _parse_idem_updated_at_as_utc のユニットテスト
# ---------------------------------------------------------------------------

class TestParseIdemUpdatedAtAsUtc:
    """
    processor._parse_idem_updated_at_as_utc を直接テストする。
    本体の関数を import して叩く。テスト内にロジックをコピーしない。
    """

    def test_utc_old_value_returns_utc_aware(self):
        """UTC で書かれた古い値は UTC aware datetime として返る。"""
        import processor
        now_utc = datetime.now(timezone.utc)
        # 31分前の UTC 値
        old_utc = (now_utc - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
        result = processor._parse_idem_updated_at_as_utc(old_utc, now_utc=now_utc)
        assert result.tzinfo == timezone.utc, "返り値は UTC aware であること"
        age = (now_utc - result).total_seconds() / 60
        assert age >= 30, f"31分前の値なので age >= 30 であること: got {age:.1f}"

    def test_utc_recent_value_returns_utc_aware(self):
        """UTC で書かれた直近の値は UTC aware datetime として返る。"""
        import processor
        now_utc = datetime.now(timezone.utc)
        # 1分前の UTC 値
        recent_utc = (now_utc - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        result = processor._parse_idem_updated_at_as_utc(recent_utc, now_utc=now_utc)
        assert result.tzinfo == timezone.utc, "返り値は UTC aware であること"
        age = (now_utc - result).total_seconds() / 60
        assert age < 30, f"1分前の値なので age < 30 であること: got {age:.1f}"

    def test_legacy_jst_value_is_converted_to_utc(self):
        """
        legacy JST (localtime) で書かれた値は UTC に変換されて返る。
        JST = UTC+9 なので、JST の "now" は UTC の "now+9h" に見える。
        UTC naive として解釈すると 5分以上未来になるため legacy 扱いになる。
        """
        import processor
        now_utc = datetime.now(timezone.utc)
        # JST naive で書かれた "現在時刻" = UTC+9h に見える値
        jst_naive_now = now_utc + timedelta(hours=9)
        jst_str = jst_naive_now.strftime("%Y-%m-%d %H:%M:%S")
        result = processor._parse_idem_updated_at_as_utc(jst_str, now_utc=now_utc)
        assert result.tzinfo == timezone.utc, "返り値は UTC aware であること"
        # JST naive を UTC に変換すると now_utc に近くなるはず（誤差 1分以内）
        diff_seconds = abs((result - now_utc).total_seconds())
        assert diff_seconds < 60, (
            f"legacy JST を UTC 変換すると now_utc に近くなるはず: diff={diff_seconds:.1f}s"
        )

    def test_legacy_jst_old_value_age_is_correct(self):
        """
        legacy JST で書かれた 31分前の値は、UTC 変換後に age >= 30 になる。
        """
        import processor
        now_utc = datetime.now(timezone.utc)
        # JST naive で書かれた "31分前" = UTC+9h-31min に見える値
        jst_naive_old = now_utc + timedelta(hours=9) - timedelta(minutes=31)
        jst_str = jst_naive_old.strftime("%Y-%m-%d %H:%M:%S")
        result = processor._parse_idem_updated_at_as_utc(jst_str, now_utc=now_utc)
        age = (now_utc - result).total_seconds() / 60
        assert age >= 30, (
            f"legacy JST 31分前の値は UTC 変換後に age >= 30 になるはず: got {age:.1f}"
        )

    def test_utc_value_slightly_in_future_is_not_legacy(self):
        """
        UTC で書かれた値が now_utc より 4分以内の未来なら legacy 扱いにならない。
        （clock skew 等で若干未来になることがある）
        """
        import processor
        now_utc = datetime.now(timezone.utc)
        # 4分後の UTC 値（5分未満なので legacy 扱いにならない）
        slightly_future = (now_utc + timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S")
        result = processor._parse_idem_updated_at_as_utc(slightly_future, now_utc=now_utc)
        assert result.tzinfo == timezone.utc
        # UTC として扱われているので、now_utc より 4分程度未来のはず
        diff = (result - now_utc).total_seconds() / 60
        assert diff < 5, f"4分後の UTC 値は legacy 扱いにならないはず: diff={diff:.1f}min"
        # 9時間引かれていないことを確認（legacy 扱いなら -9h になる）
        assert diff > -60, "9時間引かれていないこと"


# ---------------------------------------------------------------------------
# TestStaleJudgmentUtc: stale 判定が UTC 基準で動くことの統合テスト
# ---------------------------------------------------------------------------

class TestStaleJudgmentUtc:
    """
    processor.process_record を通して stale 判定が UTC 基準で動くことをテストする。
    本体の process_record を直接叩く。テスト内に stale ロジックをコピーしない。
    """

    def _setup_stale_record(self, conn, idem_key: str, updated_at_str: str):
        """指定した updated_at で processing レコードを挿入する。"""
        conn.execute(
            """INSERT INTO idempotency_keys
               (key, page_id, action, status, freee_ids, created_at, updated_at)
               VALUES (?, ?, ?, 'processing', '{}', ?, ?)""",
            (idem_key, "page-stale-001", "register", updated_at_str, updated_at_str)
        )
        conn.commit()

    def test_utc_old_processing_is_stale(self, tmp_path, monkeypatch):
        """
        UTC で書かれた 31分前の processing レコードは stale 扱いになり、
        freee 登録が再実行される。
        """
        import db as db_module
        import processor
        db_path, conn = _make_idem_db(tmp_path)
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        now_utc = datetime.now(timezone.utc)
        stale_time = (now_utc - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
        idem_key = processor._idem_key("page-stale-001", "register", {
            "sales_entry": {"issue_date": "2025-04-01", "amount": 500000},
            "purchase_entry": None,
        })
        self._setup_stale_record(conn, idem_key, stale_time)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000, "issue_date": "2025-04-01"},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }
        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())
        mock_reg = MagicMock(return_value={
            "sales_id": 1001, "purchase_id": 2001, "pca_id": None, "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=True))

        result = processor.process_record(_make_record("register"))

        assert result["status"] in ("success", "partial_error"), (
            f"UTC 31分前の processing は stale 扱いで再実行されるはず: got {result['status']}"
        )
        mock_reg.assert_called_once()
        conn.close()

    def test_utc_recent_processing_is_not_stale(self, tmp_path, monkeypatch):
        """
        UTC で書かれた 1分前の processing レコードは stale 扱いにならず、
        skip が返る。
        """
        import db as db_module
        import processor
        db_path, conn = _make_idem_db(tmp_path)
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        now_utc = datetime.now(timezone.utc)
        recent_time = (now_utc - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        idem_key = processor._idem_key("page-stale-001", "register", {
            "sales_entry": {"issue_date": "2025-04-01", "amount": 500000},
            "purchase_entry": None,
        })
        self._setup_stale_record(conn, idem_key, recent_time)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000, "issue_date": "2025-04-01"},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }
        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        mock_reg = MagicMock()
        monkeypatch.setattr("processor.register_journal", mock_reg)

        result = processor.process_record(_make_record("register"))

        assert result["status"] == "skip", (
            f"UTC 1分前の processing は stale 扱いにならず skip になるはず: got {result['status']}"
        )
        mock_reg.assert_not_called()
        conn.close()

    def test_legacy_jst_old_processing_is_stale(self, tmp_path, monkeypatch):
        """
        legacy JST (localtime) で書かれた 31分前の processing レコードは
        legacy 吸収ヘルパーで UTC 変換され、stale 扱いになる。
        """
        import db as db_module
        import processor
        db_path, conn = _make_idem_db(tmp_path)
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        now_utc = datetime.now(timezone.utc)
        # JST naive で書かれた "31分前" = UTC+9h-31min に見える値
        jst_naive_old = now_utc + timedelta(hours=9) - timedelta(minutes=31)
        jst_str = jst_naive_old.strftime("%Y-%m-%d %H:%M:%S")

        idem_key = processor._idem_key("page-stale-001", "register", {
            "sales_entry": {"issue_date": "2025-04-01", "amount": 500000},
            "purchase_entry": None,
        })
        self._setup_stale_record(conn, idem_key, jst_str)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000, "issue_date": "2025-04-01"},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }
        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        monkeypatch.setattr("processor.set_invoice_required_select", MagicMock())
        monkeypatch.setattr("processor.clear_error_set_processing", MagicMock())
        monkeypatch.setattr("processor.mark_as_error", MagicMock())
        mock_reg = MagicMock(return_value={
            "sales_id": 1001, "purchase_id": 2001, "pca_id": None, "errors": [],
        })
        monkeypatch.setattr("processor.register_journal", mock_reg)
        monkeypatch.setattr("processor.mark_as_done", MagicMock(return_value=True))

        result = processor.process_record(_make_record("register"))

        assert result["status"] in ("success", "partial_error"), (
            f"legacy JST 31分前の processing は stale 扱いで再実行されるはず: got {result['status']}"
        )
        mock_reg.assert_called_once()
        conn.close()

    def test_legacy_jst_recent_processing_is_not_stale(self, tmp_path, monkeypatch):
        """
        legacy JST (localtime) で書かれた 1分前の processing レコードは
        legacy 吸収ヘルパーで UTC 変換されても stale 扱いにならない。
        """
        import db as db_module
        import processor
        db_path, conn = _make_idem_db(tmp_path)
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)

        now_utc = datetime.now(timezone.utc)
        # JST naive で書かれた "1分前" = UTC+9h-1min に見える値
        jst_naive_recent = now_utc + timedelta(hours=9) - timedelta(minutes=1)
        jst_str = jst_naive_recent.strftime("%Y-%m-%d %H:%M:%S")

        idem_key = processor._idem_key("page-stale-001", "register", {
            "sales_entry": {"issue_date": "2025-04-01", "amount": 500000},
            "purchase_entry": None,
        })
        self._setup_stale_record(conn, idem_key, jst_str)

        journal = {
            "action": "register",
            "message": "通常登録",
            "original_status": "本部確認済",
            "job_db": "本部",
            "nyusha_date": "2025-04-01",
            "jobseeker_name": "テスト太郎",
            "sales_entry": {"amount": 500000, "issue_date": "2025-04-01"},
            "purchase_entry": {"amount": 100000},
            "pca_entry": None,
        }
        monkeypatch.setattr("processor.build_journal_entries", lambda r: journal)
        mock_reg = MagicMock()
        monkeypatch.setattr("processor.register_journal", mock_reg)

        result = processor.process_record(_make_record("register"))

        assert result["status"] == "skip", (
            f"legacy JST 1分前の processing は stale 扱いにならず skip になるはず: got {result['status']}"
        )
        mock_reg.assert_not_called()
        conn.close()


# ---------------------------------------------------------------------------
# TestIdemWritePathNoLocaltime: idempotency 書き込み経路に localtime が残っていないこと
# ---------------------------------------------------------------------------

class TestIdemWritePathNoLocaltime:
    """
    processor.py の idempotency 書き込み経路（_idem_start / _idem_save / _idem_error）に
    datetime('now','localtime') が残っていないことをソースコード上で確認する。
    """

    def _read_processor_source(self) -> str:
        import os
        processor_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "processor.py"
        )
        with open(processor_path, encoding="utf-8") as f:
            return f.read()

    def test_no_localtime_in_idem_start(self):
        """_idem_start に datetime('now','localtime') が含まれないこと。"""
        source = self._read_processor_source()
        # _idem_start 関数のブロックを抽出
        start_idx = source.find("def _idem_start(")
        end_idx = source.find("\ndef ", start_idx + 1)
        idem_start_block = source[start_idx:end_idx]
        assert "datetime('now','localtime')" not in idem_start_block, (
            "_idem_start に datetime('now','localtime') が残っている"
        )
        assert "datetime('now')" in idem_start_block, (
            "_idem_start に datetime('now') が使われていること"
        )

    def test_no_localtime_in_idem_save(self):
        """_idem_save に datetime('now','localtime') が含まれないこと。"""
        source = self._read_processor_source()
        start_idx = source.find("def _idem_save(")
        end_idx = source.find("\ndef ", start_idx + 1)
        idem_save_block = source[start_idx:end_idx]
        assert "datetime('now','localtime')" not in idem_save_block, (
            "_idem_save に datetime('now','localtime') が残っている"
        )
        assert "datetime('now')" in idem_save_block, (
            "_idem_save に datetime('now') が使われていること"
        )

    def test_no_localtime_in_idem_error(self):
        """_idem_error に datetime('now','localtime') が含まれないこと。"""
        source = self._read_processor_source()
        start_idx = source.find("def _idem_error(")
        end_idx = source.find("\ndef ", start_idx + 1)
        idem_error_block = source[start_idx:end_idx]
        assert "datetime('now','localtime')" not in idem_error_block, (
            "_idem_error に datetime('now','localtime') が残っている"
        )
        assert "datetime('now')" in idem_error_block, (
            "_idem_error に datetime('now') が使われていること"
        )

    def test_stale_judgment_uses_utc(self):
        """
        stale 判定箇所が _parse_idem_updated_at_as_utc と datetime.now(timezone.utc) を使っていること。
        """
        source = self._read_processor_source()
        assert "_parse_idem_updated_at_as_utc" in source, (
            "stale 判定に _parse_idem_updated_at_as_utc が使われていること"
        )
        assert "datetime.now(timezone.utc)" in source, (
            "stale 判定に datetime.now(timezone.utc) が使われていること"
        )
        # 旧コード _dt.now() が残っていないこと
        assert "_dt.now()" not in source, (
            "旧コード _dt.now() が残っている"
        )
