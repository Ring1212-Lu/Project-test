"""
She Light · 每日趨勢後端
===============================
安裝依賴：pip install flask flask-cors requests beautifulsoup4
啟動方式：python shelight_server.py
預設跑在：http://localhost:5050
"""

from flask import Flask, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# ─────────────────────────────────────────
# 1. Dcard 公開 API
# ─────────────────────────────────────────
def fetch_dcard():
    results = []
    try:
        boards = [
            ("身心靈", "https://www.dcard.tw/service/api/v2/posts?forum=spiritual&popular=true&limit=10"),
            ("女孩板", "https://www.dcard.tw/service/api/v2/posts?forum=girl&popular=true&limit=10"),
        ]
        for board_name, url in boards:
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                posts = r.json()
                for p in posts[:5]:
                    title   = p.get("title", "")
                    excerpt = p.get("excerpt", "")
                    like    = p.get("likeCount", 0)
                    comment = p.get("commentCount", 0)
                    if title:
                        results.append({
                            "source":     f"Dcard {board_name}",
                            "title":      title,
                            "excerpt":    excerpt[:80] if excerpt else "",
                            "engagement": like + comment,
                            "likes":      like,
                            "comments":   comment,
                        })
        results.sort(key=lambda x: x["engagement"], reverse=True)
        return results[:6]
    except Exception as e:
        return [{"source": "Dcard", "error": str(e), "title": "", "excerpt": "", "engagement": 0}]


# ─────────────────────────────────────────
# 2. Dcard 關鍵字搜尋
# ─────────────────────────────────────────
def fetch_dcard_search(keyword):
    try:
        url = f"https://www.dcard.tw/service/api/v2/search/posts?query={keyword}&limit=5"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            posts = r.json()
            return [{
                "source":     f"Dcard 搜尋「{keyword}」",
                "title":      p.get("title", ""),
                "excerpt":    p.get("excerpt", "")[:80],
                "engagement": p.get("likeCount", 0) + p.get("commentCount", 0),
                "likes":      p.get("likeCount", 0),
                "comments":   p.get("commentCount", 0),
            } for p in posts if p.get("title")]
    except:
        pass
    return []


# ─────────────────────────────────────────
# 3. PTT WomenTalk
# ─────────────────────────────────────────
def fetch_ptt():
    results = []
    try:
        boards = ["WomenTalk", "Gossiping"]
        for board in boards:
            url = f"https://www.ptt.cc/bbs/{board}/index.html"
            r   = requests.get(url, headers=HEADERS, cookies={"over18": "1"}, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            posts = soup.select("div.r-ent")
            for p in posts[:8]:
                title_el = p.select_one("div.title a")
                push_el  = p.select_one("div.nrec span")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                push  = push_el.get_text(strip=True) if push_el else "0"
                try:
                    push_num = int(push) if push.isdigit() else (99 if push == "爆" else 0)
                except:
                    push_num = 0

                keywords = ["感情","情緒","自我","療癒","星座","能量","焦慮","困擾",
                            "分手","卡住","改變","成長","覺察","心靈","諮商","放下"]
                relevant = any(k in title for k in keywords)
                results.append({
                    "source":     f"PTT {board}",
                    "title":      title,
                    "excerpt":    "",
                    "engagement": push_num,
                    "likes":      push_num,
                    "comments":   0,
                    "relevant":   relevant,
                })

        results.sort(key=lambda x: (x.get("relevant", False), x["engagement"]), reverse=True)
        return results[:6]
    except Exception as e:
        return [{"source": "PTT", "error": str(e), "title": "", "excerpt": "", "engagement": 0}]


# ─────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────
@app.route("/api/trends", methods=["GET"])
def get_trends():
    today = datetime.now().strftime("%Y-%m-%d")

    dcard_general = fetch_dcard()
    dcard_crystal = fetch_dcard_search("水晶")
    dcard_healing = fetch_dcard_search("療癒")
    dcard_energy  = fetch_dcard_search("能量")
    ptt_data      = fetch_ptt()

    # 合併 Dcard 資料並去重
    all_dcard = dcard_general + dcard_crystal + dcard_healing + dcard_energy
    seen = set()
    dcard_unique = []
    for item in all_dcard:
        t = item.get("title", "")
        if t and t not in seen:
            seen.add(t)
            dcard_unique.append(item)
    dcard_unique.sort(key=lambda x: x["engagement"], reverse=True)

    return jsonify({
        "date":   today,
        "dcard":  dcard_unique[:8],
        "ptt":    ptt_data,
        "status": {
            "dcard": "ok" if dcard_unique else "empty",
            "ptt":   "ok" if ptt_data   else "empty",
        }
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "She Light 後端運行中"})


if __name__ == "__main__":
    print("=" * 50)
    print("  She Light · 每日趨勢後端")
    print("  http://localhost:5050")
    print("=" * 50)
    app.run(port=5050, debug=False)
