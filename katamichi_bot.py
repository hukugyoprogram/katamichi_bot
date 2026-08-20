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
# 3. 片道GOの空き枠取得（柔軟判定 & デバッグ出力）
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
        
        # trだけでなく、li, dl, div などのブロック要素も走査
        targets = soup.find_all(["tr", "li", "dl", "div"])

        for target in targets:
            text = " ".join(target.get_text(separator=" ", strip=True).split())

            # 受付終了枠・長すぎる親要素・短すぎるヘッダーをスキップ
            if "受付終了" in text or "受付を終了" in text:
                continue
            if len(text) > 300 or len(text) < 20:
                continue

            # 電話番号の検出
            tel_match = re.search(r"(0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}|0\d{9,10})", text)
            if not tel_match:
                continue

            # 「店」「営業所」「空港」などレンタカー拠点らしき文言が含まれるか
            if not any(k in text for k in ["店", "営業所", "空港", "出発", "返却"]):
                continue

            # 要素内のセル・子要素からテキストを分割抽出
            parts = [el.get_text(strip=True) for el in target.find_all(["td", "th", "dd", "dt", "p", "span"]) if el.get_text(strip=True)]
            
            # 各要素が取れている場合はそれを使用、取れない場合は全文から抽出
            departure = parts[0] if len(parts) > 0 else "公式参照"
            return_area = parts[1] if len(parts) > 1 else "公式参照"
            period = parts[2] if len(parts) > 2 else "公式参照"
            car_type = parts[3] if len(parts) > 3 else "公式参照"
            tel = tel_match.group(1).replace(" ", "-")

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
                    "raw_text": text[:60] # デバッグ確認用
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

    available_slots = fetch_available_slots()
    print(f"🔍 現在の予約可能枠: {len(available_slots)} 件")

    # 取得できた枠があればログに先頭サンプルを出力
    for s in available_slots[:3]:
        print(f"  └ 取得データ例: {s['raw_text']}")

    # 初回起動時は一括記録して終了（一斉ツイート防止）
    if len(history) == 0 and len(available_slots) > 0:
        print("🛡️ 【初期化モード】初回起動のため、現在の枠を履歴に記録して終了します。")
        for slot in available_slots:
            history.add(slot["id"])
        save_history(history)
        print(f"💾 {len(available_slots)} 件を履歴に保存しました。次回以降の新着枠から投稿されます。")
        return

    new_post_count = 0
    for slot in available_slots:
        if slot["id"] not in history:
            print(f"✨ 新着枠: {slot['departure']} ➔ {slot['return_area']}（TEL: {slot['tel']}）")
            
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
