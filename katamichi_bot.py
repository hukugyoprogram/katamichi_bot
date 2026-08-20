import os
import re
import time
import json
import requests
from bs4 import BeautifulSoup
import tweepy
from dotenv import load_dotenv

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

def fetch_available_slots():
    url = "https://cp.toyota.jp/rentacar/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        slots = []
        blocks = soup.find_all(["tr", "li", "div", "dl"])
        
        for block in blocks:
            text = " ".join(block.get_text(separator=" ", strip=True).split())

            # ① 受付終了・規約・長文は除外
            if any(ng in text for ng in ["受付終了", "受付を終了", "予約済", "最大48時間", "免責補償", "指定店舗となります"]):
                continue
            if len(text) > 200 or len(text) < 15:
                continue

            # ② 本物の予約枠に必須の「電話番号」をチェック（なければ即スキップ）
            tel_match = re.search(r"(0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}|0\d{9,10})", text)
            if not tel_match:
                continue

            # ③ 出発店舗・返却店舗・利用期間の抽出
            dep_match = re.search(r"出発(?:店舗)?[:：\s]*([^\s,]+(?:店|営業所|空港)?)", text)
            ret_match = re.search(r"返却(?:地域|エリア|店舗)?[:：\s]*([^\s,]+)", text)
            period_match = re.search(r"(\d{1,2}[/月]\d{1,2}[^\s]*\s*[〜～~\-ー]\s*\d{1,2}[/月]\d{1,2}[^\s]*)", text)
            car_match = re.search(r"(?:車種|クラス)[:：\s]*([^\s,]+)", text)

            departure = dep_match.group(1) if dep_match else ""
            return_area = ret_match.group(1) if ret_match else ""
            period = period_match.group(1) if period_match else "公式参照"
            car_type = car_match.group(1) if car_match else "指定なし"
            tel = tel_match.group(1).replace(" ", "-")

            # ゴミデータ・見出し文字列の徹底排除
            if not departure or not return_area:
                continue
            if departure in ["店舗", "出発店舗"] or return_area in ["店舗", "返却店舗", "車種"]:
                continue
            if "指定店舗" in departure or "指定店舗" in return_area:
                continue

            slot_id = f"{departure}_{return_area}_{period}_{car_type}_{tel}"

            if not any(s["id"] == slot_id for s in slots):
                slots.append({
                    "id": slot_id,
                    "departure": departure,
                    "return_area": return_area,
                    "period": period,
                    "car_type": car_type,
                    "tel": tel,
                    "url": url,
                })

        return slots

    except Exception as e:
        print(f"⚠️ データ取得エラー: {e}")
        return []

def post_to_x(slot) -> bool:
    if not client:
        return False

    tweet_text = (
        f"🚗【片道GO！新着枠】\n\n"
        f"📍 出発店舗：{slot['departure']}\n"
        f"🏁 返却店舗：{slot['return_area']}\n"
        f"🗓️ 出発期間：{slot['period']}\n"
        f"🚘 対象車種：{slot['car_type']}\n"
        f"📞 予約TEL：{slot['tel']}\n\n"
        f"詳細・公式ページ👇\n"
        f"{slot['url']}\n\n"
        f"#片道GO #レンタカー"
    )

    try:
        response = client.create_tweet(text=tweet_text)
        print(f"✅ ポスト完了！ [Tweet ID: {response.data['id']}]")
        return True
    except tweepy.TweepyException as e:
        print(f"❌ X投稿エラー: {e}")
        return False

def main():
    history = load_history()
    print(f"📁 過去の投稿履歴: {len(history)} 件を読込")

    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠: {len(available_slots)} 件")

    new_post_count = 0
    for slot in available_slots:
        if slot["id"] not in history:
            print(f"✨ 新着枠を検知: {slot['departure']} ➔ {slot['return_area']}（{slot['car_type']} / TEL: {slot['tel']}）")
            
            if post_to_x(slot):
                history.add(slot["id"])
                new_post_count += 1
                time.sleep(2)
            else:
                history.add(slot["id"])

    save_history(history)
    print(f"🎉 処理完了: {new_post_count} 件の新着枠を処理しました。")

if __name__ == "__main__":
    main()
