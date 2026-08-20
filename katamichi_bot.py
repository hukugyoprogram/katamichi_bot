import os
import re
import time
import json
import requests
from bs4 import BeautifulSoup
import tweepy
from dotenv import load_dotenv

# ==========================================================
# 1. 環境設定 & X API認証
# ==========================================================
load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")

client = None
if all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
    try:
        client = tweepy.Client(
            consumer_key=API_KEY,
            consumer_secret=API_SECRET,
            access_token=ACCESS_TOKEN,
            access_token_secret=ACCESS_TOKEN_SECRET,
        )
    except Exception as e:
        print(f"⚠️ X クライアント初期化エラー: {e}")

HISTORY_FILE = "posted_slots.json"

# ==========================================================
# 2. 過去の投稿履歴の読み書き
# ==========================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_history(history_set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=2)

# ==========================================================
# 3. 片道GOの空き枠（予約可能枠）だけを抽出する関数
# ==========================================================
def fetch_available_slots():
    url = "https://cp.toyota.jp/rentacar/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")

        slots = []

        # テーブルの各行（tr）およびリスト要素を精査
        target_elements = soup.find_all(["tr", "li", "dl"])

        for el in target_elements:
            text = " ".join(el.get_text(separator=" ", strip=True).split())

            # 1. 予約済みの枠やヘッダーをピンポイントで除外
            if "受付を終了" in text or "予約済み" in text or "受付終了" in text:
                continue
            if len(text) < 25:
                continue

            # 2. 電話番号（店舗TEL）が存在するか
            tel_match = re.search(r"(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})", text)
            if not tel_match:
                continue

            # 3. 日付（利用期間）が存在するか
            period_match = re.search(r"(\d{1,2}[/月]\d{1,2}[^\s]*\s*〜\s*\d{1,2}[/月]\d{1,2}[^\s]*)", text)
            if not period_match:
                continue

            # 4. 出発店舗・返却先エリアの抽出
            dep_match = re.search(r"(?:出発|店舗)[:：\s]*([^\s,]+(?:店|営業所|空港)?)", text)
            ret_match = re.search(r"(?:返却|エリア)[:：\s]*([^\s,]+)", text)

            departure = dep_match.group(1) if dep_match else "店舗詳細は公式参照"
            return_area = ret_match.group(1) if ret_match else "返却先は公式参照"
            tel = tel_match.group(1)
            period = period_match.group(1)

            # ゴミデータ排除（見出し文字列の誤取得防止）
            if departure in ["店舗", "出発店舗"] or return_area in ["店舗", "車種", "返却店舗"]:
                continue

            slot_id = f"{departure}_{return_area}_{period}_{tel}"

            if not any(s["id"] == slot_id for s in slots):
                slots.append({
                    "id": slot_id,
                    "departure": departure,
                    "return_area": return_area,
                    "period": period,
                    "tel": tel,
                    "url": url,
                })

        return slots

    except Exception as e:
        print(f"⚠️ データ取得エラー: {e}")
        return []

# ==========================================================
# 4. X（Twitter）ポスト関数
# ==========================================================
def post_to_x(slot) -> bool:
    if not client:
        print("⚠️ X APIが未設定です。")
        return False

    tweet_text = (
        f"🚗【片道GO！空き枠検知】\n\n"
        f"📍 出発店舗：{slot['departure']}\n"
        f"🏁 返却地域：{slot['return_area']}\n"
        f"🗓️ 利用期間：{slot['period']}\n"
        f"📞 予約TEL：{slot['tel']}\n\n"
        f"詳細・公式ページ👇\n"
        f"{slot['url']}\n\n"
        f"#片道GO #レンタカー #格安移動"
    )

    try:
        response = client.create_tweet(text=tweet_text)
        print(f"✅ ポスト完了！ [Tweet ID: {response.data['id']}]")
        return True
    except tweepy.TweepyException as e:
        print(f"❌ X投稿エラー: {e}")
        return False

# ==========================================================
# 5. メイン実行処理（安全初期化ガード付き）
# ==========================================================
def main():
    history = load_history()
    print(f"📁 過去の投稿履歴: {len(history)} 件を読込")

    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠: {len(available_slots)} 件")

    # 初回起動時（履歴ファイルが空の場合）は一括保存して即終了（スパム・制限防止）
    if len(history) == 0 and len(available_slots) > 0:
        print("🛡️ 【初期化モード】初回のため現在の空き枠を履歴に記録して終了します。")
        for slot in available_slots:
            history.add(slot["id"])
        save_history(history)
        print(f"💾 {len(available_slots)} 件を履歴に初期保存しました。次回以降の新着から投稿されます。")
        return

    new_post_count = 0
    for slot in available_slots:
        if slot["id"] not in history:
            print(f"✨ 新着空き枠を検知: {slot['departure']} ➔ {slot['return_area']}（TEL: {slot['tel']}）")
            
            if post_to_x(slot):
                history.add(slot["id"])
                new_post_count += 1
                time.sleep(2)
            else:
                history.add(slot["id"])

    save_history(history)
    print(f"🎉 処理完了: {new_post_count} 件の新規空き枠を処理しました。")

if __name__ == "__main__":
    main()
