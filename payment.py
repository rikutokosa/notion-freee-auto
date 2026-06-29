"""
全銀フォーマット（FB形式）振込データ生成モジュール

SBIネット銀行 総合振込対応
仕様: 全国銀行協会 総合振込フォーマット（固定長テキスト）
"""
import re
from datetime import datetime
from typing import Optional

from freee_client import get_partner_bank

# ============================================================
# 依頼人情報（住信SBIネット銀行 法人口座）
# ============================================================
REQUESTER_CODE = "2010523001"       # 依頼人コード（10桁）住信SBIネット銀行
REQUESTER_NAME_KANA = "ﾍﾞｱｰｽﾞﾅﾋﾞ"  # 依頼人名（カナ・半角20文字以内）
REQUESTER_BANK_CODE = "0038"        # 住信SBIネット銀行
REQUESTER_BRANCH_CODE = "106"       # 支店番号
REQUESTER_ACCOUNT_TYPE = "1"        # 1=普通
REQUESTER_ACCOUNT_NUMBER = "1356501"


# ============================================================
# 全銀フォーマット定数
# ============================================================
RECORD_LENGTH = 120  # 1レコード120バイト（固定長）


def _to_halfkana(text: str) -> str:
    """全角カナ・英数字を半角に変換する"""
    if not text:
        return ""
    # 全角英数字→半角
    result = ""
    for c in text:
        code = ord(c)
        if 0xFF01 <= code <= 0xFF5E:
            result += chr(code - 0xFEE0)
        elif c == "\u3000":
            result += " "
        else:
            result += c
    # 全角カナ→半角カナ（主要文字）
    zen_to_han = {
        "ア": "ｱ", "イ": "ｲ", "ウ": "ｳ", "エ": "ｴ", "オ": "ｵ",
        "カ": "ｶ", "キ": "ｷ", "ク": "ｸ", "ケ": "ｹ", "コ": "ｺ",
        "サ": "ｻ", "シ": "ｼ", "ス": "ｽ", "セ": "ｾ", "ソ": "ｿ",
        "タ": "ﾀ", "チ": "ﾁ", "ツ": "ﾂ", "テ": "ﾃ", "ト": "ﾄ",
        "ナ": "ﾅ", "ニ": "ﾆ", "ヌ": "ﾇ", "ネ": "ﾈ", "ノ": "ﾉ",
        "ハ": "ﾊ", "ヒ": "ﾋ", "フ": "ﾌ", "ヘ": "ﾍ", "ホ": "ﾎ",
        "マ": "ﾏ", "ミ": "ﾐ", "ム": "ﾑ", "メ": "ﾒ", "モ": "ﾓ",
        "ヤ": "ﾔ", "ユ": "ﾕ", "ヨ": "ﾖ",
        "ラ": "ﾗ", "リ": "ﾘ", "ル": "ﾙ", "レ": "ﾚ", "ロ": "ﾛ",
        "ワ": "ﾜ", "ヲ": "ｦ", "ン": "ﾝ",
        "ァ": "ｧ", "ィ": "ｨ", "ゥ": "ｩ", "ェ": "ｪ", "ォ": "ｫ",
        "ッ": "ｯ", "ャ": "ｬ", "ュ": "ｭ", "ョ": "ｮ",
        "ガ": "ｶﾞ", "ギ": "ｷﾞ", "グ": "ｸﾞ", "ゲ": "ｹﾞ", "ゴ": "ｺﾞ",
        "ザ": "ｻﾞ", "ジ": "ｼﾞ", "ズ": "ｽﾞ", "ゼ": "ｾﾞ", "ゾ": "ｿﾞ",
        "ダ": "ﾀﾞ", "ヂ": "ﾁﾞ", "ヅ": "ﾂﾞ", "デ": "ﾃﾞ", "ド": "ﾄﾞ",
        "バ": "ﾊﾞ", "ビ": "ﾋﾞ", "ブ": "ﾌﾞ", "ベ": "ﾍﾞ", "ボ": "ﾎﾞ",
        "パ": "ﾊﾟ", "ピ": "ﾋﾟ", "プ": "ﾌﾟ", "ペ": "ﾍﾟ", "ポ": "ﾎﾟ",
        "ヴ": "ｳﾞ", "ー": "ｰ", "。": "｡", "「": "｢", "」": "｣",
        "、": "､", "・": "･",
    }
    result2 = ""
    for c in result:
        result2 += zen_to_han.get(c, c)
    return result2


def _pad_right(s: str, length: int, char: str = " ") -> str:
    """右スペース埋め（半角換算）"""
    s = str(s)[:length]
    return s.ljust(length, char)


def _pad_left(s: str, length: int, char: str = "0") -> str:
    """左ゼロ埋め"""
    s = str(s)[:length]
    return s.zfill(length)


def _account_type_code(account_type: str) -> str:
    """口座種別コード: 普通=1, 当座=2, 貯蓄=4"""
    mapping = {"ordinary": "1", "checking": "2", "savings": "4",
               "普通": "1", "当座": "2", "貯蓄": "4"}
    return mapping.get(account_type, "1")


def build_fb_file(transfer_targets: list, transfer_date: str) -> tuple:
    """
    全銀フォーマット（FB形式）のテキストを生成する。

    Args:
        transfer_targets: get_payment_deals()の transfer_targets リスト
        transfer_date: ヘッダー用の振込日（YYYYMMDD形式）。各明細は due_date を使用。

    Returns:
        (fb_text, summary) のタプル
    """
    lines = []
    today = datetime.now()

    # ヘッダーの振込指定日：対象仕訳の最も早いdue_dateを使用（なければtransfer_dateを使用）
    due_dates = [t.get("due_date", "") for t in transfer_targets if t.get("due_date")]
    if due_dates:
        earliest = min(due_dates)
        header_transfer_mmdd = earliest[5:7] + earliest[8:10]
    else:
        header_transfer_mmdd = transfer_date[4:8]

    # ヘッダーレコード（1レコード目）
    # 種別コード(1) + データ区分(2) + 種別コード(1) + 依頼人コード(10) + 依頼人名(40) +
    # 振込指定日(4) + 仕向銀行番号(4) + 仕向銀行名(15) + 仕向支店番号(3) + 仕向支店名(15) +
    # 預金種目(1) + 口座番号(7) + ダミー(17)
    header = (
        "1"                                              # 種別コード
        + "21"                                           # データ区分（総合振込=21）
        + "0"                                            # 種別コード
        + _pad_left(REQUESTER_CODE, 10)                  # 依頼人コード
        + _pad_right(_to_halfkana(REQUESTER_NAME_KANA), 40)  # 依頼人名
        + header_transfer_mmdd                           # 振込指定日（MMDD）
        + _pad_left(REQUESTER_BANK_CODE, 4)              # 仕向銀行番号
        + _pad_right("スミシンジエスビアイ", 15)               # 仕向銀行名
        + _pad_left(REQUESTER_BRANCH_CODE, 3)            # 仕向支店番号
        + _pad_right("", 15)                             # 仕向支店名
        + _account_type_code(REQUESTER_ACCOUNT_TYPE)    # 預金種目
        + _pad_left(REQUESTER_ACCOUNT_NUMBER, 7)         # 口座番号
        + _pad_right("", 17)                             # ダミー
    )
    assert len(header) == RECORD_LENGTH, f"ヘッダー長エラー: {len(header)}"
    lines.append(header)

    # データレコード（振込1件につき1レコード）
    total_amount = 0
    valid_count = 0
    skipped = []

    for t in transfer_targets:
        partner_id = t.get("partner_id")
        if not partner_id:
            skipped.append({"deal_id": t.get("deal_id"), "reason": "取引先IDなし"})
            continue

        bank = get_partner_bank(partner_id)
        if not bank.get("bank_code") or not bank.get("account_number"):
            skipped.append({
                "deal_id": t.get("deal_id"),
                "partner_name": t.get("partner_name"),
                "reason": "銀行口座未登録",
            })
            continue

        amount = int(t.get("amount", 0))
        if amount <= 0:
            skipped.append({"deal_id": t.get("deal_id"), "reason": "金額0以下"})
            continue

        # 振込指定日は各仕訳の決済期日（due_date）を使用。未設定の場合はヘッダーの日付で代用
        due_date_raw = t.get("due_date", "") or ""
        if due_date_raw and len(due_date_raw) == 10:
            # YYYY-MM-DD → MMDD
            record_transfer_date = due_date_raw[5:7] + due_date_raw[8:10]
        else:
            record_transfer_date = transfer_date[4:8]  # ヘッダーの日付で代用

        account_name = _to_halfkana(bank.get("account_name", ""))

        # データレコード（120バイト固定）
        # 種別コード(1) + 仕向銀行番号(4) + 仕向銀行名(15) + 仕向支店番号(3) + 仕向支店名(15) +
        # ダミー(4) + 預金種目(1) + 口座番号(7) + 受取人名(30) + 振込金額(10) +
        # 新規コード(1) + EDI情報(20) + 振込区分(1) + ダミー(7) + 識別コード(1)
        record = (
            "2"                                                      # 種別コード
            + _pad_left(bank.get("bank_code", ""), 4)                # 仕向銀行番号
            + _pad_right(_to_halfkana(bank.get("bank_name", "")), 15)  # 仕向銀行名
            + _pad_left(bank.get("branch_code", ""), 3)              # 仕向支店番号
            + _pad_right(_to_halfkana(bank.get("branch_name", "")), 15)  # 仕向支店名
            + _pad_right("", 4)                                      # ダミー
            + _account_type_code(bank.get("account_type", "ordinary"))  # 預金種目
            + _pad_left(bank.get("account_number", ""), 7)           # 口座番号
            + _pad_right(account_name, 30)                           # 受取人名
            + _pad_left(str(amount), 10)                             # 振込金額
            + "0"                                                    # 新規コード（0=その他）
            + _pad_right("", 20)                                     # EDI情報
            + "0"                                                    # 振込区分（0=電信）
            + _pad_right("", 7)                                      # ダミー
            + "0"                                                    # 識別コード
        )
        assert len(record) == RECORD_LENGTH, f"データレコード長エラー: {len(record)} deal_id={t.get('deal_id')}"
        lines.append(record)
        total_amount += amount
        valid_count += 1

    # トレーラーレコード
    # 種別コード(1) + 合計件数(6) + 合計金額(12) + ダミー(101)
    trailer = (
        "8"
        + _pad_left(str(valid_count), 6)
        + _pad_left(str(total_amount), 12)
        + _pad_right("", 101)
    )
    assert len(trailer) == RECORD_LENGTH, f"トレーラー長エラー: {len(trailer)}"
    lines.append(trailer)

    # エンドレコード
    end = "9" + _pad_right("", 119)
    assert len(end) == RECORD_LENGTH, f"エンドレコード長エラー: {len(end)}"
    lines.append(end)

    return "\r\n".join(lines), {
        "valid_count": valid_count,
        "total_amount": total_amount,
        "skipped": skipped,
    }
