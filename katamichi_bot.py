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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
                data = json.load(f)
                return set([x for x in data if "公式参照" not in x and "指定店舗" not in x])
        except Exception:
            return set()
    return set()

def save_history(history_set):
    clean_history = [x for x in history_set if "公式参照" not in x and "指定店舗" not in x]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_history, f, ensure_ascii=False, indent=2)

# ==========================================================
# 3. 実際にAPIキーで使えるモデルを自動検出
# ==========================================================
def get_available_model_name():
    """現在のアカウントで generateContent が実行可能なモデルを自動取得"""
    try:
        models = list(gemini_client.models.list())
        for m in models:
            actions = getattr(m, "supported_actions", []) or []
            if "generateContent" in actions:
                name = m.name.replace("models/", "")
                if "flash" in name:
                    return name
        for m in models:
            name = m.name.replace("models/", "")
            if "flash" in name:
                return name
    except Exception as e:
        print(f"⚠️ モデル一覧取得警告: {e}")
    return "gemini-1.5-flash-8b"

# ==========================================================
# 4. AIを使って空き枠を抽出する関数
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

    target_model = get_available_model_name()
    print(f"🤖 自動検出された有効AIモデル: {target_model}")

    prompt = f"""
Webサイトのテキストからトヨタレンタカー「片道GO！」の【現在予約受付中（空き枠）】のみを抽出してください。

【厳格な除外条件】
1. 「受付終了」「受付を終了」「予約済」「満車」と書かれている枠は【絶対に抽出しないでください】。
2. 「注意書き」「利用手順」「規約」などの文章は【絶対に除外】してください。
3. 出発店舗・返却店舗・電話番号が具体的に書かれていないものは除外してください。
4. 受付中の枠がない場合は空の配列 `[]` を返してください。

【出力形式 (JSON)】
[
  {{
    "departure": "具体的な出発店舗名（例: 盛岡駅南口店）",
    "return_area": "具体的な返却店舗・エリア名",
    "period": "出発期間",
    "car_type": "車種",
    "tel": "具体的な予約電話番号"
  }}
]

【Webテキスト】
{page_text[:10000]}
"""

    res_text = None
    for attempt in range(1, 4):
        try:
            print(f"🤖 AI解析中... (試行 {attempt}/3)")
            res = gemini_client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            res_text = res.text
            break
        except Exception as e:
            print(f"⚠️ 試行 {attempt}/3 エラー: {e}")
            if attempt < 3:
                time.sleep(3)

    if not res_text:
        print("❌ AI解析に失敗しました。次回巡回時に再試行します。")
        return []

    try:
        parsed_slots = json.loads(res_text)
        valid_slots = []
        for item in parsed_slots:
            dep = item.get("departure", "").strip()
            ret = item.get("return_area", "").strip()
            per = item.get("period", "").strip()
            car = item.get("car_type", "").strip()
            tel = item.get("tel", "").strip()

            if not dep or not ret or not tel:
                continue
            if any(ng in dep for ng in ["公式参照", "指定店舗", "店舗"]) or any(ng in ret for ng in ["公式参照", "指定店舗", "店舗"]):
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
        print(f"⚠️ JSON解析エラー: {e}")
        return []

# ==========================================================
# 5. X（Twitter）ポスト関数
# ==========================================================
def post_to_x(slot) -> bool:
    if not x_client:
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
# 6. メイン実行処理
# ==========================================================
def main():
    history = load_history()
    print(f"📁 クリーンな過去履歴: {len(history)} 件を読込")

    available_slots = fetch_available_slots_with_ai()
    print(f"🔍 AIが検知した受付中枠: {len(available_slots)} 件")

    new_slots = [s for s in available_slots if s["id"] not in history]

    if len(new_slots) >= 3:
        print(f"🛡️ {len(new_slots)} 件の枠を検知。初回同期のため履歴に一括保存します（投稿スキップ）。")
        for s in new_slots:
            history.add(s["id"])
        save_history(history)
        print(f"💾 {len(new_slots)} 件を綺麗に履歴へ保存しました。")
        return

    new_post_count = 0
    for slot in new_slots:
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
