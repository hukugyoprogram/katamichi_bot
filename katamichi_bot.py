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

if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
    raise ValueError("⚠️ APIキーが設定されていません。")

client = tweepy.Client(
    consumer_key=API_KEY,
    consumer_secret=API_SECRET,
    access_token=ACCESS_TOKEN,
    access_token_secret=ACCESS_TOKEN_SECRET,
)

HISTORY_FILE = "posted_slots.json"

# ==========================================================
# 2. 過去の投稿履歴を読み書きする関数
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
# 3. 片道GOの情報を取得・パースする関数
# ==========================================================
def fetch_katamichi_slots():
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
        elements = soup.find_all(["tr", "div", "li"])

        for el in elements:
            text = el.get_text(separator=" ", strip=True)
            if "出発" in text and "返却" in text and ("TEL" in text or "0" in text):
                clean_text = " ".join(text.split())
                slot_id = clean_text[:60]

                if slot_id and not any(s["id"] == slot_id for s in slots):
                    dep_match = re.search(r"出発(?:店舗)?[:：\s]*([^\s,]+(?:店|営業所)?)", clean_text)
                    departure = dep_match.group(1) if dep_match else "詳細参照"

                    ret_match = re.search(r"返却(?:地域|エリア|店舗)?[:：\s]*([^\s,]+)", clean_text)
                    return_area = ret_match.group(1) if ret_match else "詳細参照"

                    period_match = re.search(r"(\d{1,2}[/月]\d{1,2}[^\s]*\s*〜\s*\d{1,2}[/月]\d{1,2}[^\s]*)", clean_text)
                    period = period_match.group(1) if period_match else "公式ページ参照"

                    tel_match = re.search(r"(\d{2,4}-\d{2,4}-\d{4}|0\d{9,10})", clean_text)
                    tel = tel_match.group(1) if tel_match else "店舗TEL確認"

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
# 4. ポスト関数
# ==========================================================
def post_to_x(slot) -> bool:
    tweet_text = (
        f"🚗【片道GO！新着枠】\n\n"
        f"📍 出発店舗：{slot['departure']}\n"
        f"🏁 返却地域：{slot['return_area']}\n"
        f"🗓️ 出発期間：{slot['period']}\n"
        f"📞 予約TEL：{slot['tel']}\n\n"
        f"詳細・公式👇\n"
        f"{slot['url']}\n\n"
        f"#片道GO #レンタカー #格安"
    )

    try:
        response = client.create_tweet(text=tweet_text)
        print(f"✅ ポスト成功！ [Tweet ID: {response.data['id']}]")
        return True
    except tweepy.TweepyException as e:
        print(f"❌ X投稿エラー: {e}")
        return False

# ==========================================================
# 5. メイン処理（1回実行して終了）
# ==========================================================
def main():
    history = load_history()
    print(f"📁 過去の投稿履歴: {len(history)} 件を読込")

    current_slots = fetch_katamichi_slots()
    print(f"🔍 現在の掲載枠: {len(current_slots)} 件")

    new_count = 0
    for slot in current_slots:
        if slot["id"] not in history:
            print(f"✨ 新着枠を検知: {slot['departure']} ➔ {slot['return_area']}")
            if post_to_x(slot):
                history.add(slot["id"])
                new_count += 1
                time.sleep(2)

    save_history(history)
    print(f"🎉 処理完了: {new_count} 件の新着をポストしました。")

if __name__ == "__main__":
    main()
