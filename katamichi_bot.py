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
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        slots = []
        rows = soup.find_all("tr")
        print(f"📄 サイト内の全テーブル行数: {len(rows)} 行")

        for row in rows:
            # 行全体のテキストを取得
            row_text = row.get_text(separator=" ", strip=True)

            # ①「受付終了」の枠は除外
            if "受付終了" in row_text or "受付を終了" in row_text:
                continue

            # ② ヘッダー行や説明文を除外（「出発店舗」「返却店舗」という見出し文字そのままの行など）
            if "利用可能時間" in row_text or "最大48時間" in row_text or "免責補償" in row_text:
                continue

            # 行の中にあるセル（td または th）をすべて取得
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            # 各セルの文字をそのまま取得
            cell_texts = [re.sub(r"\s+", " ", c.get_text(strip=True)) for c in cells]

            # 見出し行そのものの場合はスキップ
            if cell_texts[0] in ["出発店舗", "店舗", "No.", "出発地"]:
                continue

            # 電話番号の抽出
            tel_match = re.search(r"(0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}|0\d{9,10})", row_text)
            tel = tel_match.group(1).replace(" ", "-") if tel_match else "電話番号は公式参照"

            # サイトの並び順通りに代入
            departure = cell_texts[0] if len(cell_texts) > 0 else "公式参照"
            return_area = cell_texts[1] if len(cell_texts) > 1 else "公式参照"
            period = cell_texts[2] if len(cell_texts) > 2 else "公式参照"
            car_type = cell_texts[3] if len(cell_texts) > 3 else "公式参照"

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

# ==========================================================
# 4. X（Twitter）ポスト関数
# ==========================================================
def post_to_x(slot) -> bool:
    if not client:
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

    # 1. 現在の予約可能枠を取得
    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠: {len(available_slots)} 件")

    # 【初期化モード】初回起動時は今ある全枠を記録して終了（爆撃投稿防止）
    if len(history) == 0 and len(available_slots) > 0:
        print("🛡️ 【初期化モード】初回起動のため、現在の枠を履歴に記録して終了します。")
        for slot in available_slots:
            history.add(slot["id"])
        save_history(history)
        print(f"💾 {len(available_slots)} 件を履歴に保存しました。次回以降の新着枠から投稿されます。")
        return

    # 2. 前回見つけていなかった（履歴にない）新着枠だけを抽出して投稿
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
