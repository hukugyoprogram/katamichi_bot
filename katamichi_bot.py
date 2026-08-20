import os
import time
import json
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import tweepy
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# 1. API初期化
# ==========================================================
# Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# X API
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
# 3. AI（Gemini）を使って空き枠を抽出する関数
# ==========================================================
def fetch_available_slots_with_ai():
    url = "https://cp.toyota.jp/rentacar/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(separator="\n", strip=True)
    except Exception as e:
        print(f"⚠️ サイト接続エラー: {e}")
        return []

    if not gemini_client:
        print("❌ GEMINI_API_KEY が設定されていません。")
        return []

    prompt = f"""
あなたはWebサイトのテキストからレンタカーの空き枠を正確に抽出するアシスタントです。
以下のテキストから、トヨタレンタカー「片道GO！」の空き枠（現在予約可能な枠）をすべて抽出してください。

【厳守ルール】
1. 「受付終了」「受付を終了」「予約済」「満車」と書かれている枠は【絶対に除外】してください。
2. 注意書き、規約、案内文などのゴミデータは【絶対に除外】してください。
3. 実際に予約可能な空き枠が1件もない場合は、空の配列 `[]` を返してください。
4. 返却フォーマットは純粋なJSON配列のみにしてください。

【出力キー（各要素）】
- departure: 出発店舗名（例: 盛岡駅南口店）
- return_area: 返却店舗または返却地域（例: 仙台空港店）
- period: 出発期間（例: 8/20〜8/25）
- car_type: 車種（例: ヤリス等）
- tel: 予約電話番号（例: 019-xxx-xxxx）

【Webページテキスト】
{page_text[:10000]}
"""

    try:
        print("🤖 AI（Gemini）が空き枠を解析中...")
        res = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        parsed_slots = json.loads(res.text)
        valid_slots = []
        for item in parsed_slots:
            dep = item.get("departure", "").strip()
            ret = item.get("return_area", "").strip()
            per = item.get("period", "").strip()
            car = item.get("car_type", "").strip()
            tel = item.get("tel", "").strip()

            if not dep or not ret:
                continue

            slot_id = f"{dep}_{ret}_{per}_{car}_{tel}"
            valid_slots.append({
                "id": slot_id,
                "departure": dep,
                "return_area": ret,
                "period": per,
                "car_type": car,
                "tel": tel,
                "url": url,
            })

        return valid_slots

    except Exception as e:
        print(f"⚠️ AI解析エラー: {e}")
        return []

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

    available_slots = fetch_available_slots_with_ai()
    print(f"🔍 AIが検知した現在の予約可能枠: {len(available_slots)} 件")

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
