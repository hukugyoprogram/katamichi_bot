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
# 3. 片道GOの情報を抽出する関数
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

    slots = []
    
    # ページ内のテキストブロックから各枠を走査
    # 各枠には必ず「出発店舗」「返却店舗」「出発期間」が含まれる
    blocks = soup.find_all(["div", "li", "tr", "section", "article", "dl"])

    for block in blocks:
        text = " ".join(block.get_text(separator=" ", strip=True).split())

        # 枠として必要な最小要素（出発店舗 & 返却店舗）
        if not ("出発店舗" in text and "返却店舗" in text):
            continue

        # ページ全体のラッパーなど大きすぎる要素、説明文ブロックはスキップ
        if len(text) > 400 or "免責補償料" in text or "基本料金のご案内" in text:
            continue

        # ① 受付終了の枠は除外
        if any(closed_kw in text for closed_kw in ["受付終了", "受付を終了", "予約済", "満車"]):
            continue

        # ② 各項目をラベルベースで正確に抽出
        # 出発店舗
        dep_match = re.search(r"出発店舗\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s*返却店舗|\s*車種|\s*出発期間|$)", text)
        # 返却店舗
        ret_match = re.search(r"返却店舗\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s*車種|\s*出発期間|\s*車両条件|$)", text)
        # 出発期間
        period_match = re.search(r"出発期間\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s*さらに|\s*車種|\s*予約電話番号|\s*車両条件|$)", text)
        # 車種
        car_match = re.search(r"車種\s*[:：]?\s*([^\s]+(?:\s+[^\s]+)*?)(?=\s*車両条件|\s*予約電話番号|\s*出発期間|$)", text)
        # 予約電話番号
        tel_match = re.search(r"(?:予約電話番号|TEL|電話番号)?\s*[:：]?\s*([^\s]*0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}[^\s]*)", text)

        departure = dep_match.group(1).strip() if dep_match else "公式参照"
        return_area = ret_match.group(1).strip() if ret_match else "公式参照"
        period = period_match.group(1).strip() if period_match else "公式参照"
        car_type = car_match.group(1).strip() if car_match else "指定なし"
        
        # 電話番号の整形
        raw_tel = tel_match.group(1).strip() if tel_match else "公式参照"
        tel_num = re.search(r"(0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}|0\d{9,10})", raw_tel)
        tel = tel_num.group(1).replace(" ", "-") if tel_num else raw_tel

        # 見出し文字列そのものや空データは除外
        if departure in ["返却店舗", "車種"] or return_area in ["車種", "出発期間"]:
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

# ==========================================================
# 4. X（Twitter）ポスト関数
# ==========================================================
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

# ==========================================================
# 5. メイン実行処理
# ==========================================================
def main():
    history = load_history()
    print(f"📁 過去の投稿履歴: {len(history)} 件を読込")

    # 1. 現在の予約可能枠を取得（受付終了除外済み）
    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠: {len(available_slots)} 件")

    # 2. 前回の履歴（JSON）に入っていない新着枠だけを抽出して投稿
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
