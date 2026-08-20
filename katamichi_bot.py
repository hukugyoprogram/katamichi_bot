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
# 3. 片道GOの情報を抽出する関数（診断カウンター付き）
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

    response_text = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.encoding = "utf-8"
            if response.status_code == 200:
                response_text = response.text
                break
        except requests.exceptions.RequestException as e:
            print(f"⚠️ 接続リトライ ({attempt}/3): {e}")
            time.sleep(2)

    if not response_text:
        print("❌ サイトに接続できませんでした。")
        return []

    soup = BeautifulSoup(response_text, "html.parser")

    total_detected = 0
    closed_count = 0
    available_slots = []

    # サイト内の全要素から「出発」と「返却」を含む枠ブロックを探索
    candidates = soup.find_all(["tr", "li", "dl", "div"])

    for el in candidates:
        text = " ".join(el.get_text(separator=" ", strip=True).split())

        # 共通の規約文や大きすぎる親ブロック、ヘッダー単体は除外
        if len(text) > 300 or len(text) < 20:
            continue
        if "免責補償" in text or "最大48時間" in text or "利用手順" in text:
            continue

        # 枠として必要なキーワードが含まれているか判定
        if "出発" not in text or "返却" not in text:
            continue

        # 電話番号（予約用TEL）の存在判定
        tel_match = re.search(r"(0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}|0\d{9,10})", text)
        if not tel_match:
            continue

        total_detected += 1

        # ① 受付終了の枠をカウント
        if "受付終了" in text or "受付を終了" in text or "予約済" in text:
            closed_count += 1
            continue

        # ② 受付中（空き枠）のデータを抽出
        tel = tel_match.group(1).replace(" ", "-")

        # 表記ゆれを許容した店舗・期間・車種の抽出
        dep_match = re.search(r"出発(?:店舗)?[:：\s]*([^\s,]+(?:店|営業所|空港)?)", text)
        ret_match = re.search(r"返却(?:地域|エリア|店舗)?[:：\s]*([^\s,]+)", text)
        period_match = re.search(r"(\d{1,2}[/月]\d{1,2}[^\s]*\s*[〜～~\-ー]\s*\d{1,2}[/月]\d{1,2}[^\s]*)", text)
        car_match = re.search(r"(?:車種|クラス)[:：\s]*([^\s,]+)", text)

        departure = dep_match.group(1) if dep_match else "出発店舗は公式参照"
        return_area = ret_match.group(1) if ret_match else "返却店舗は公式参照"
        period = period_match.group(1) if period_match else "出発期間は公式参照"
        car_type = car_match.group(1) if car_match else "車種は公式参照"

        # 見出し文字列の誤取得を除外
        if departure in ["店舗", "出発店舗"] and return_area in ["店舗", "返却店舗"]:
            continue

        slot_id = f"{departure}_{return_area}_{period}_{car_type}_{tel}"

        if not any(s["id"] == slot_id for s in available_slots):
            available_slots.append({
                "id": slot_id,
                "departure": departure,
                "return_area": return_area,
                "period": period,
                "car_type": car_type,
                "tel": tel,
                "url": url,
            })

    print(f"📊 【診断】検知した全枠数: {total_detected} 件")
    print(f"🔒 【診断】受付終了の枠数: {closed_count} 件")
    print(f"🟢 【診断】予約可能（空き）枠数: {len(available_slots)} 件")

    return available_slots

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

    available_slots = fetch_available_slots()

    # 初回起動時の安全ガード（空き枠がある場合のみ一括登録）
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
