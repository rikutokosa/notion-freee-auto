"""tests/test_db_backup_scripts.py

backup_db.sh / restore_db.sh が db.py の実 DB パスと一致していることを確認するテスト。

テスト方針:
- 本番 DB は操作しない（スクリプトの内容を読み取るだけ）
- backup_db.sh / restore_db.sh を実行しない（bash -n 構文チェックは別途実施）
- db.py の DB 名と一致していることをコード上で確認する
"""
import os
import re

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
_BACKUP_SCRIPT = os.path.join(_SCRIPTS_DIR, "backup_db.sh")
_RESTORE_SCRIPT = os.path.join(_SCRIPTS_DIR, "restore_db.sh")
_DB_PY = os.path.join(os.path.dirname(__file__), "..", "db.py")


def _read(path: str) -> str:
    with open(os.path.abspath(path), encoding="utf-8") as f:
        return f.read()


class TestBackupScriptDbPath:
    """backup_db.sh が正しい DB パスを使っていることを確認する"""

    def test_backup_contains_chat_history_db(self):
        """backup_db.sh に chat_history.db が含まれる"""
        content = _read(_BACKUP_SCRIPT)
        assert "chat_history.db" in content, (
            "backup_db.sh に chat_history.db が含まれていません"
        )

    def test_backup_does_not_contain_notion_freee_db(self):
        """backup_db.sh に古い /data/notion_freee.db が残っていない"""
        content = _read(_BACKUP_SCRIPT)
        # コメント行を除いた実コード行に notion_freee.db がないことを確認
        code_lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        assert "notion_freee.db" not in code_only, (
            "backup_db.sh の実コード行に古い notion_freee.db が残っています:\n"
            + "\n".join(l for l in code_lines if "notion_freee.db" in l)
        )

    def test_backup_contains_railway_volume_mount_path(self):
        """backup_db.sh に RAILWAY_VOLUME_MOUNT_PATH が含まれる"""
        content = _read(_BACKUP_SCRIPT)
        assert "RAILWAY_VOLUME_MOUNT_PATH" in content, (
            "backup_db.sh に RAILWAY_VOLUME_MOUNT_PATH が含まれていません"
        )

    def test_backup_uses_default_db_path_variable(self):
        """backup_db.sh が DEFAULT_DB_PATH 変数を定義して DB_PATH に使っている"""
        content = _read(_BACKUP_SCRIPT)
        assert "DEFAULT_DB_PATH=" in content, (
            "backup_db.sh に DEFAULT_DB_PATH= が含まれていません"
        )
        assert 'DB_PATH="${1:-${DEFAULT_DB_PATH}}"' in content, (
            "backup_db.sh の DB_PATH が DEFAULT_DB_PATH を使っていません"
        )

    def test_backup_file_name_uses_chat_history(self):
        """backup_db.sh のバックアップファイル名が chat_history_backup を使っている"""
        content = _read(_BACKUP_SCRIPT)
        assert "chat_history_backup" in content, (
            "backup_db.sh のバックアップファイル名が chat_history_backup になっていません"
        )
        assert "notion_freee_backup" not in content, (
            "backup_db.sh に古い notion_freee_backup が残っています"
        )


class TestRestoreScriptDbPath:
    """restore_db.sh が正しい DB パスを使っていることを確認する"""

    def test_restore_contains_chat_history_db(self):
        """restore_db.sh に chat_history.db が含まれる"""
        content = _read(_RESTORE_SCRIPT)
        assert "chat_history.db" in content, (
            "restore_db.sh に chat_history.db が含まれていません"
        )

    def test_restore_does_not_contain_notion_freee_db(self):
        """restore_db.sh に古い /data/notion_freee.db が残っていない"""
        content = _read(_RESTORE_SCRIPT)
        # コメント行を除いた実コード行に notion_freee.db がないことを確認
        code_lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        assert "notion_freee.db" not in code_only, (
            "restore_db.sh の実コード行に古い notion_freee.db が残っています:\n"
            + "\n".join(l for l in code_lines if "notion_freee.db" in l)
        )

    def test_restore_contains_railway_volume_mount_path(self):
        """restore_db.sh に RAILWAY_VOLUME_MOUNT_PATH が含まれる"""
        content = _read(_RESTORE_SCRIPT)
        assert "RAILWAY_VOLUME_MOUNT_PATH" in content, (
            "restore_db.sh に RAILWAY_VOLUME_MOUNT_PATH が含まれていません"
        )

    def test_restore_uses_default_db_path_variable(self):
        """restore_db.sh が DEFAULT_DB_PATH 変数を定義して DB_PATH に使っている"""
        content = _read(_RESTORE_SCRIPT)
        assert "DEFAULT_DB_PATH=" in content, (
            "restore_db.sh に DEFAULT_DB_PATH= が含まれていません"
        )
        assert 'DB_PATH="${2:-${DEFAULT_DB_PATH}}"' in content, (
            "restore_db.sh の DB_PATH が DEFAULT_DB_PATH を使っていません"
        )


class TestDbNameConsistency:
    """db.py の DB 名とスクリプトの DB 名が一致していることを確認する"""

    def _extract_db_name_from_db_py(self) -> str:
        """db.py の _DB_PATH から DB ファイル名を抽出する"""
        content = _read(_DB_PY)
        # _DB_PATH = os.path.join(_VOLUME_PATH, "chat_history.db") のような行を探す
        match = re.search(r'_DB_PATH\s*=\s*os\.path\.join\([^,]+,\s*["\']([^"\']+\.db)["\']', content)
        assert match, f"db.py から _DB_PATH の DB ファイル名を抽出できませんでした:\n{content}"
        return match.group(1)

    def test_backup_script_db_name_matches_db_py(self):
        """backup_db.sh の DB 名が db.py の _DB_PATH と一致する"""
        db_name = self._extract_db_name_from_db_py()
        backup_content = _read(_BACKUP_SCRIPT)
        assert db_name in backup_content, (
            f"backup_db.sh に db.py の DB 名 '{db_name}' が含まれていません"
        )

    def test_restore_script_db_name_matches_db_py(self):
        """restore_db.sh の DB 名が db.py の _DB_PATH と一致する"""
        db_name = self._extract_db_name_from_db_py()
        restore_content = _read(_RESTORE_SCRIPT)
        assert db_name in restore_content, (
            f"restore_db.sh に db.py の DB 名 '{db_name}' が含まれていません"
        )

    def test_db_py_db_name_is_chat_history(self):
        """db.py の DB 名が chat_history.db であることを確認する（期待値の明示）"""
        db_name = self._extract_db_name_from_db_py()
        assert db_name == "chat_history.db", (
            f"db.py の DB 名が chat_history.db ではありません: {db_name}"
        )
