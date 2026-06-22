"""eBay 買い手メッセージ自動返信スクリプト（タスク5）

対応パターン:
  ①「いつ届く？」系 → 配送ポリシーの回答（英語）
  ②「写真を見せて」系 → 倉庫梱包中のため難しいと回答（英語）

両アカウント対応:
  - tsujou (japanesehappinessshop)
  - senmon (japanese_selectshop)

使い方:
    python ebay_message_responder.py              # 両アカウント・本番実行
    python ebay_message_responder.py --dry-run    # 送信せず確認のみ
    python ebay_message_responder.py --account tsujou   # 通常のみ
    python ebay_message_responder.py --account senmon   # 専門のみ
    python ebay_message_responder.py --days 3           # 過去3日分を対象（デフォルト: 1日）
"""

import sys
import os
import re
import time
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# =========================================================
# eBay config (タスク2の設定を共用)
# =========================================================
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CWORK_DIR = os.path.dirname(_THIS_DIR)
_TASK2_DIR = os.path.join(_CWORK_DIR, "ﾀｽｸ2_売上管理表")
# GitHub Actions: ebay_config.py は同ディレクトリに生成される
# ローカルPC: ﾀｽｸ2_売上管理表/ にある（両方 sys.path に追加）
for _p in [_THIS_DIR, _TASK2_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ebay_config

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}

# ログファイルパス
LOG_PATH = os.path.join(_THIS_DIR, "responder_log.txt")

# =========================================================
# 返信テンプレート
# =========================================================

REPLY_SHIPPING = (
    "Thank you for your purchase.\n"
    "I will send it as soon as possible within the deadline. "
    "* eBay's shipping policy is 10 business days "
    "(excluding Saturdays, Sundays, and holidays)"
)

REPLY_PHOTOS = (
    "It's difficult to take photos because it's in a warehouse and packed.\n"
    "However, I will send it as soon as possible, thank you for your consideration."
)

# =========================================================
# メッセージ分類キーワード
# =========================================================

SHIPPING_KEYWORDS = [
    # 英語
    "when", "arrive", "arrival", "delivery", "ship", "shipping", "shipped",
    "receipt", "receive", "days", "date", "timeline", "estimated", "dispatch",
    "tracking", "status", "how long", "expect",
    # スペイン語
    "seguimiento", "enviado", "cuando", "rastreo", "envio", "envío",
    "llegara", "llegará", "entrega", "llegar", "despacho",
    # 日本語
    "いつ", "届く", "到着", "発送", "配送", "受取", "日数", "受け取", "いつ頃",
    "着く", "期日", "予定", "見込",
]

PHOTOS_KEYWORDS = [
    # 英語
    "photo", "picture", "image", "photos", "pictures", "images",
    "show", "more photo", "another photo", "other photo", "additional photo",
    "see", "view", "can you send", "can i see",
    # スペイン語
    "foto", "fotos", "imagen", "imagenes", "imágenes", "mostrar", "ver",
    # 日本語
    "写真", "画像", "見せ", "他の", "もっと", "追加", "別の",
]


def classify_message(body_text):
    """メッセージ本文を分類する

    Returns:
        "shipping"   → 配送時期の質問
        "photos"     → 追加写真の要求
        None         → 該当パターンなし
    """
    text_lower = (body_text or "").lower()

    # 写真要求は配送より先にチェック（被りを避けるため）
    for kw in PHOTOS_KEYWORDS:
        if kw.lower() in text_lower:
            return "photos"

    for kw in SHIPPING_KEYWORDS:
        if kw.lower() in text_lower:
            return "shipping"

    return None


# =========================================================
# eBay Trading API ヘルパー
# =========================================================

def _trading_headers(account_name, call_name):
    acc = ebay_config.get_account(account_name)
    return {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-APP-NAME": acc["APP_ID"],
        "X-EBAY-API-DEV-NAME": acc["DEV_ID"],
        "X-EBAY-API-CERT-NAME": acc["CERT_ID"],
        "Content-Type": "text/xml",
    }


def _call_trading_api(account_name, call_name, xml_body):
    """Trading APIを呼び出してElementTreeを返す"""
    headers = _trading_headers(account_name, call_name)
    resp = requests.post(TRADING_API_URL, data=xml_body.encode("utf-8"), headers=headers, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return root


# =========================================================
# GetMemberMessages
# =========================================================

def get_unanswered_messages(account_name, days=1):
    """未回答メッセージを取得する

    Args:
        account_name: "tsujou" or "senmon"
        days: 過去何日分を取得するか（デフォルト1日）

    Returns:
        list of dict:
            message_id, parent_message_id, sender_id,
            item_id, subject, body, creation_date
    """
    acc = ebay_config.get_account(account_name)
    now_utc = datetime.now(timezone.utc)
    start_time = (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_time = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<GetMemberMessagesRequest xmlns="urn:ebay:apis:eBLBaseComponents">\n'
        '  <RequesterCredentials>\n'
        '    <eBayAuthToken>' + acc["TOKEN"] + '</eBayAuthToken>\n'
        '  </RequesterCredentials>\n'
        '  <MailMessageType>All</MailMessageType>\n'
        '  <MessageStatus>Unanswered</MessageStatus>\n'
        '  <StartCreationTime>' + start_time + '</StartCreationTime>\n'
        '  <EndCreationTime>' + end_time + '</EndCreationTime>\n'
        '</GetMemberMessagesRequest>\n'
    )

    try:
        root = _call_trading_api(account_name, "GetMemberMessages", xml_body)
    except Exception as e:
        print("  [ERROR] GetMemberMessages 失敗: " + str(e)[:120])
        return []

    # エラーチェック
    ack = root.findtext("e:Ack", namespaces=NS) or root.findtext("Ack", "")
    if ack not in ("Success", "Warning"):
        errors = root.findall(".//e:ShortMessage", NS) or root.findall(".//ShortMessage")
        err_msg = ", ".join(e.text or "" for e in errors)
        print("  [ERROR] API応答: " + ack + " / " + err_msg)
        return []

    # デバッグ: 生XMLを保存（解析に問題があるとき確認用）
    debug_xml_path = os.path.join(_THIS_DIR, "last_api_response_" + account_name + ".xml")
    try:
        with open(debug_xml_path, "wb") as f:
            f.write(ET.tostring(root, encoding="unicode").encode("utf-8"))
    except Exception:
        pass

    messages = []

    # eBay GetMemberMessages のレスポンス構造:
    # GetMemberMessagesResponse
    #   └ MemberMessage
    #       └ MemberMessageExchange (1件ごと)
    #           └ Question         ← 買い手からのメッセージ本体
    #               ├ MessageID
    #               ├ SenderID
    #               ├ Subject
    #               ├ Body
    #               └ ItemID
    #           └ Response         ← 既回答があれば存在

    # Clark記法で直接検索（ElementTreeのorバグ回避: テキストノードはfalsyになる）
    CURN = "{urn:ebay:apis:eBLBaseComponents}"

    def _text(node, tag):
        """Clark記法でテキスト取得（None安全）"""
        el = node.find(CURN + tag)
        return el.text if el is not None and el.text else ""

    exchanges = root.findall(".//" + CURN + "MemberMessageExchange")

    for exchange in exchanges:
        question = exchange.find(CURN + "Question")
        if question is None:
            continue

        # MessageType チェック:
        #   AskSellerQuestion       → 買い手からの新規質問  ← 対象
        #   ResponseToASQQuestion   → 既存スレッドへの買い手返信 ← キーワードあれば返信
        msg_type = _text(question, "MessageType")

        # ItemID は Question 内ではなく exchange/Item/ItemID にある
        item_el = exchange.find(CURN + "Item")
        item_id = _text(item_el, "ItemID") if item_el is not None else ""

        messages.append({
            "message_id":        _text(question, "MessageID"),
            "parent_message_id": _text(question, "MessageID"),
            "sender_id":         _text(question, "SenderID"),
            "item_id":           item_id,
            "subject":           _text(question, "Subject"),
            "body":              _text(question, "Body"),
            "creation_date":     _text(question, "CreationDate"),
            "message_type":      msg_type,
        })

    return messages


# =========================================================
# AddMemberMessageRTQ (返信)
# =========================================================

def send_reply(account_name, item_id, parent_message_id, recipient_id, reply_body, dry_run=False):
    """買い手メッセージに返信する

    Args:
        account_name: "tsujou" or "senmon"
        item_id: eBay ItemID
        parent_message_id: 返信先の MessageID
        recipient_id: 送信先 (= 買い手の SenderID)
        reply_body: 返信本文
        dry_run: True なら送信せず内容を表示するだけ

    Returns:
        bool: 成功したか
    """
    if dry_run:
        print("    [DRY-RUN] 送信しない。返信内容:")
        for line in reply_body.splitlines():
            print("      " + line)
        return True

    acc = ebay_config.get_account(account_name)
    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<AddMemberMessageRTQRequest xmlns="urn:ebay:apis:eBLBaseComponents">\n'
        '  <RequesterCredentials>\n'
        '    <eBayAuthToken>' + acc["TOKEN"] + '</eBayAuthToken>\n'
        '  </RequesterCredentials>\n'
        '  <ItemID>' + item_id + '</ItemID>\n'
        '  <MemberMessage>\n'
        '    <ParentMessageID>' + parent_message_id + '</ParentMessageID>\n'
        '    <Body>' + reply_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + '</Body>\n'
        '    <RecipientID>' + recipient_id + '</RecipientID>\n'
        '  </MemberMessage>\n'
        '</AddMemberMessageRTQRequest>\n'
    )

    try:
        root = _call_trading_api(account_name, "AddMemberMessageRTQ", xml_body)
        ack = root.findtext("e:Ack", namespaces=NS) or root.findtext("Ack", "")
        if ack in ("Success", "Warning"):
            return True
        else:
            errors = root.findall(".//e:ShortMessage", NS) or root.findall(".//ShortMessage")
            err_msg = ", ".join(e.text or "" for e in errors)
            print("    [ERROR] 返信失敗: " + ack + " / " + err_msg)
            return False
    except Exception as e:
        print("    [ERROR] 返信API呼び出し失敗: " + str(e)[:120])
        return False


# =========================================================
# ログ
# =========================================================

def _log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[" + timestamp + "] " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# =========================================================
# メインロジック
# =========================================================

def process_account(account_name, days=1, dry_run=False):
    """1アカウント分のメッセージを処理する

    Returns:
        dict: {"checked": N, "replied": N, "skipped": N}
    """
    acc = ebay_config.get_account(account_name)
    _log("=" * 60)
    _log("アカウント: " + acc["name"] + " (" + acc["seller"] + ")")
    _log("過去 " + str(days) + " 日分の未回答メッセージを取得中...")

    messages = get_unanswered_messages(account_name, days=days)
    _log("  取得件数: " + str(len(messages)) + " 件")

    stats = {"checked": len(messages), "replied": 0, "skipped": 0}

    for i, msg in enumerate(messages):
        _log("")
        _log("  [" + str(i + 1) + "/" + str(len(messages)) + "]"
             " Sender=" + msg["sender_id"] +
             " Item=" + msg["item_id"] +
             " MsgID=" + msg["message_id"])
        _log("  Subject : " + (msg["subject"] or "（なし）")[:80])
        _log("  Body    : " + (msg["body"] or "（なし）")[:120].replace("\n", " "))
        _log("  Type    : " + msg.get("message_type", ""))

        pattern = classify_message(msg["subject"] + " " + msg["body"])

        if pattern == "shipping":
            _log("  → パターン①: 配送時期の質問 → 返信する")
            reply_text = REPLY_SHIPPING
        elif pattern == "photos":
            _log("  → パターン②: 追加写真の要求 → 返信する")
            reply_text = REPLY_PHOTOS
        else:
            _log("  → パターン該当なし → スキップ（手動対応）")
            stats["skipped"] += 1
            continue

        if not msg["item_id"]:
            _log("  [WARN] ItemIDがないため返信できません → スキップ")
            stats["skipped"] += 1
            continue

        ok = send_reply(
            account_name=account_name,
            item_id=msg["item_id"],
            parent_message_id=msg["parent_message_id"],
            recipient_id=msg["sender_id"],
            reply_body=reply_text,
            dry_run=dry_run,
        )

        if ok:
            _log("  [OK] 返信" + ("（DRY-RUN）" if dry_run else "送信") + "完了")
            stats["replied"] += 1
        else:
            _log("  [FAIL] 返信失敗 → 手動確認が必要")
            stats["skipped"] += 1

        time.sleep(1)  # API呼び出し間隔

    return stats


def main():
    # 引数パース
    dry_run = "--dry-run" in sys.argv
    days = 1
    target_accounts = ["tsujou", "senmon"]

    for arg in sys.argv[1:]:
        if arg.startswith("--days="):
            try:
                days = int(arg.split("=", 1)[1])
            except ValueError:
                pass
        elif arg == "--days" and sys.argv.index(arg) + 1 < len(sys.argv):
            idx = sys.argv.index(arg)
            try:
                days = int(sys.argv[idx + 1])
            except (ValueError, IndexError):
                pass
        elif arg.startswith("--account="):
            target_accounts = [arg.split("=", 1)[1]]
        elif arg == "--account" and sys.argv.index(arg) + 1 < len(sys.argv):
            idx = sys.argv.index(arg)
            if idx + 1 < len(sys.argv):
                target_accounts = [sys.argv[idx + 1]]

    _log("=" * 60)
    _log("eBay 買い手メッセージ自動返信 開始")
    _log("モード: " + ("DRY-RUN（送信なし）" if dry_run else "本番"))
    _log("対象: " + str(target_accounts))
    _log("=" * 60)

    total = {"checked": 0, "replied": 0, "skipped": 0}

    for account_name in target_accounts:
        try:
            stats = process_account(account_name, days=days, dry_run=dry_run)
            for k in total:
                total[k] += stat