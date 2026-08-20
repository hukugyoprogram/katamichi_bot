import os
import time
import json
import requests
from bs4 import BeautifulSoup
import tweepy
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# 1. API初期化
# ==========================================================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")

x_client = None
if all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
    try:
        x_client = tweepy.Client(
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
# 3. HTML構造から正確に空き枠のみを抽出する関数
# ==========================================================
def fetch_available_slots():
    url = "https://cp.toyota.jp/rentacar/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        print(f"⚠️ サイト接続エラー: {e}")
        return []

    available_slots = []

    # 重複を防ぐため「出発店舗順リスト」側のみを取得
    start_list = soup.find("ul", id="service-items-shop-type-start")
    if not start_list:
        items = soup.find_all("li", class_="service-item")
    else:
        items = start_list.find_all("li", class_="service-item")

    for item in items:
        body = item.find("div", class_="service-item__body")
        if not body:
            continue

        # 【超重要】show-entry-end クラスが付いている枠は「受付終了」なので除外
        classes = body.get("class", [])
        if "show-entry-end" in classes:
            continue

        # 各要素の抽出
        shop_start_el = item.find("div", class_="service-item__shop-start")
        shop_return_el = item.find("div", class_="service-item__shop-return")
        date_el = item.find("div", class_="service-item__date")
        car_el = item.find("div", class_="service-item__info__car-type")
        tel_el = item.find("div", class_="service-item__reserve-tel")

        departure = shop_start_el.find_all("p")[-1].get_text(separator=" ", strip=True) if shop_start_el else ""
        return_area = shop_return_el.find_all("p")[-1].get_text(separator=" ", strip=True) if shop_return_el else ""
        period = date_el.find_all("p")[-1].get_text(strip=True) if date_el else ""
        car_type = car_el.find_all("p")[-1].get_text(strip=True) if car_el else ""
        tel = tel_el.get_text(strip=True) if tel_el else ""

        if not departure or not return_area:
            continue

        slot_id = f"{departure}_{return_area}_{period}_{car_type}_{tel}"

        available_slots.append({
            "id": slot_id,
            "departure": departure,
            "return_area": return_area,
            "period": period,
            "car_type": car_type,
            "tel": tel,
            "url": url,
        })

    return available_slots

# ==========================================================
# 4. X（Twitter）ポスト関数
# ==========================================================
def post_to_x(slot) -> bool:
    if not x_client:
        print("⚠️ X APIが未設定です。")
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
        response = x_client.create_tweet(text=tweet_text)
        print(f"✅ ポスト完了！ [Tweet ID: {response.data['id']}]")
        return True
    except tweepy.TweepyException as e:
        print(f"❌ X投稿エラー: {e}")
        return False

# ==========================================================
# 5. メイン実行処理
# ==========================================================
def main():
    history = load_history()
    print(f"📁 過去の投稿履歴: {len(history)} 件を読込")

    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠（受付中のみ）: {len(available_slots)} 件")

    new_post_count = 0
    for slot in available_slots:
        if slot["id"] not in history:
            print(f"✨ 新着枠を検知: {slot['departure']} ➔ {slot['return_area']}（{slot['car_type']} / TEL: {slot['tel']}）")
            if post_to_x(slot):
                new_post_count += 1
                time.sleep(2)
            history.add(slot["id"])

    save_history(history)
    print(f"🎉 処理完了: {new_post_count} 件の新着枠を処理しました。")

if __name__ == "__main__":
    main()
