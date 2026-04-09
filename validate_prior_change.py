"""
用 543 筆真實歷史數據驗證 BAYES_PRIOR 50→45 的影響

分析：
1. 有多少信號是靠「小樣本 + prior 拉高」通過 min_score=45 的？
2. PRIOR=45 後哪些信號會被過濾？過濾掉的勝率如何？
3. 對整體勝率的影響（過濾掉的是好信號還是壞信號？）
"""
import json
from collections import defaultdict

with open("learning_data.json") as f:
    data = json.load(f)

history = data["history"]
print(f"總驗證數據: {len(history)} 筆")
print()

# ====== 按策略和 regime 分組統計 ======
MIN_SCORE = 45
BAYES_K = 20

def calc_adj_rate(raw_wr, n, prior, k=BAYES_K):
    if n < 5:
        return None
    return prior + (raw_wr - prior) * n / (n + k)


# 先統計每個策略的真實勝率（from history）
strat_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
for h in history:
    strat = h["strategy"]
    result = h.get("result")
    if result == "win":
        strat_stats[strat]["wins"] += 1
    elif result == "lose":
        strat_stats[strat]["losses"] += 1
    strat_stats[strat]["total"] += 1

print("=" * 70)
print("1. 各策略真實驗證勝率（543筆歷史數據）")
print("=" * 70)
print(f"{'策略':<15} {'勝':>4} {'敗':>4} {'總':>4} {'勝率':>7} {'adj@50':>7} {'adj@45':>7}")
print("-" * 60)

core_strats = {}
for strat, s in sorted(strat_stats.items(), key=lambda x: -x[1]["total"]):
    total = s["wins"] + s["losses"]
    if total == 0:
        continue
    wr = s["wins"] / total * 100
    ar50 = calc_adj_rate(wr, total, 50)
    ar45 = calc_adj_rate(wr, total, 45)
    core_strats[strat] = {"wr": wr, "n": total, "wins": s["wins"], "losses": s["losses"]}
    ar50_s = f"{ar50:.1f}" if ar50 else "N/A"
    ar45_s = f"{ar45:.1f}" if ar45 else "N/A"
    print(f"{strat:<15} {s['wins']:>4} {s['losses']:>4} {total:>4} {wr:>6.1f}% {ar50_s:>7} {ar45_s:>7}")


# ====== 分析每筆信號在不同 PRIOR 下的 score ======
print()
print("=" * 70)
print("2. 逐筆分析：PRIOR=50 vs PRIOR=45 的影響")
print("=" * 70)

# 重建每筆信號的 score（history 裡有 rate 和 score）
pass_50_only = []  # PRIOR=50 通過但 PRIOR=45 被擋
pass_both = []     # 兩者都通過
block_both = []    # 兩者都被擋
block_50_pass_45 = []  # 不可能（45 更嚴格）

for h in history:
    score_50 = h.get("score", 0)
    rate_50 = h.get("rate", 0)
    result = h.get("result", "expired")
    strat = h["strategy"]
    regime = h.get("regime", "unknown")

    # 反推 n 和 raw_wr（從 adj_rate = prior + (raw - prior) * n/(n+k)）
    # 但我們沒有 n 和 raw_wr，只有最終 rate (adj_rate) 和 score
    # score = adj_rate * weight * global_penalty * regime_adj
    # 我們可以用 rate (which is adj_rate with prior=50) 來推算 prior=45 的 rate
    # adj_rate_50 = 50 + (raw - 50) * n/(n+20)
    # adj_rate_45 = 45 + (raw - 45) * n/(n+20)
    # adj_rate_45 = adj_rate_50 - 5 + 5 * n/(n+20)
    #             = adj_rate_50 - 5 * (1 - n/(n+20))
    #             = adj_rate_50 - 5 * 20/(n+20)
    #             = adj_rate_50 - 100/(n+20)

    # 問題：我們不知道 n，但可以用 score/rate 的比值推算 multiplier
    # multiplier = score / rate (= weight * global_penalty * regime_adj)
    if rate_50 > 0:
        multiplier = score_50 / rate_50
    else:
        multiplier = 1.0

    # 但不知道 n，我們用幾個合理的 n 值來測試
    # 從策略的 total 數據估算 n 的範圍
    pass_50 = score_50 >= MIN_SCORE

    h["_pass_50"] = pass_50
    h["_multiplier"] = multiplier
    h["_result"] = result


# 因為我們不知道每筆信號的個別 n，用更直接的方法：
# 看 score 在 45 附近的信號（45-50 分之間）—— 這些最容易受 PRIOR 影響
print()
print(f"{'分數區間':<15} {'總數':>5} {'勝':>4} {'敗':>4} {'過期':>4} {'勝率':>7}")
print("-" * 50)

score_bands = [
    ("< 40", 0, 40),
    ("40-44.9", 40, 45),
    ("45-49.9", 45, 50),
    ("50-54.9", 50, 55),
    ("55-59.9", 55, 60),
    (">= 60", 60, 999),
]

for label, lo, hi in score_bands:
    band = [h for h in history if lo <= h.get("score", 0) < hi]
    wins = sum(1 for h in band if h.get("result") == "win")
    losses = sum(1 for h in band if h.get("result") == "lose")
    expired = sum(1 for h in band if h.get("result") == "expired")
    total_wl = wins + losses
    wr = wins / total_wl * 100 if total_wl > 0 else 0
    print(f"{label:<15} {len(band):>5} {wins:>4} {losses:>4} {expired:>4} {wr:>6.1f}%")


# ====== PRIOR=45 的 score 估算 ======
print()
print("=" * 70)
print("3. PRIOR=50→45 的影響估算")
print("=" * 70)
print()
print("adj_rate_45 = adj_rate_50 - 100/(n+20)")
print("當 n 未知時，用不同 n 估算 score 下降幅度：")
print()
print(f"{'n':>4} {'adj_rate 下降':>14} {'score 下降(mult=1)':>20}")
print("-" * 45)
for n in [5, 7, 10, 15, 20, 30, 50, 100]:
    delta = 100 / (n + 20)
    print(f"{n:>4} {delta:>13.2f} {delta:>19.2f}")


# ====== 最關鍵的分析：45-50 分區間的信號品質 ======
print()
print("=" * 70)
print("4. 關鍵區間 (score 45-50)：這些信號的品質如何？")
print("=" * 70)

marginal = [h for h in history if 45 <= h.get("score", 0) < 50]
print(f"\n  score 45-50 的信號共 {len(marginal)} 筆")

if marginal:
    m_wins = sum(1 for h in marginal if h.get("result") == "win")
    m_losses = sum(1 for h in marginal if h.get("result") == "lose")
    m_expired = sum(1 for h in marginal if h.get("result") == "expired")
    m_total = m_wins + m_losses
    m_wr = m_wins / m_total * 100 if m_total > 0 else 0

    print(f"  勝: {m_wins}, 敗: {m_losses}, 過期: {m_expired}")
    print(f"  勝率: {m_wr:.1f}%")

    # 按策略細分
    print(f"\n  {'策略':<15} {'勝':>4} {'敗':>4} {'勝率':>7}")
    print(f"  {'-'*35}")
    strat_marginal = defaultdict(lambda: {"w": 0, "l": 0})
    for h in marginal:
        if h.get("result") == "win":
            strat_marginal[h["strategy"]]["w"] += 1
        elif h.get("result") == "lose":
            strat_marginal[h["strategy"]]["l"] += 1

    for strat, s in sorted(strat_marginal.items(), key=lambda x: -(x[1]["w"]+x[1]["l"])):
        t = s["w"] + s["l"]
        wr = s["w"] / t * 100 if t > 0 else 0
        print(f"  {strat:<15} {s['w']:>4} {s['l']:>4} {wr:>6.1f}%")

    # 按 regime 細分
    print(f"\n  {'Regime':<15} {'勝':>4} {'敗':>4} {'勝率':>7}")
    print(f"  {'-'*35}")
    regime_marginal = defaultdict(lambda: {"w": 0, "l": 0})
    for h in marginal:
        if h.get("result") == "win":
            regime_marginal[h.get("regime", "?")]["w"] += 1
        elif h.get("result") == "lose":
            regime_marginal[h.get("regime", "?")]["l"] += 1

    for reg, s in sorted(regime_marginal.items(), key=lambda x: -(x[1]["w"]+x[1]["l"])):
        t = s["w"] + s["l"]
        wr = s["w"] / t * 100 if t > 0 else 0
        print(f"  {reg:<15} {s['w']:>4} {s['l']:>4} {wr:>6.1f}%")


# ====== 比較 >=50 vs 45-50 的勝率差距 ======
print()
print("=" * 70)
print("5. 高分 vs 邊緣信號的勝率比較")
print("=" * 70)

high = [h for h in history if h.get("score", 0) >= 50]
h_wins = sum(1 for h in high if h.get("result") == "win")
h_losses = sum(1 for h in high if h.get("result") == "lose")
h_total = h_wins + h_losses
h_wr = h_wins / h_total * 100 if h_total > 0 else 0

low_pass = [h for h in history if 45 <= h.get("score", 0) < 50]
l_wins = sum(1 for h in low_pass if h.get("result") == "win")
l_losses = sum(1 for h in low_pass if h.get("result") == "lose")
l_total = l_wins + l_losses
l_wr = l_wins / l_total * 100 if l_total > 0 else 0

blocked = [h for h in history if h.get("score", 0) < 45]
b_wins = sum(1 for h in blocked if h.get("result") == "win")
b_losses = sum(1 for h in blocked if h.get("result") == "lose")
b_total = b_wins + b_losses
b_wr = b_wins / b_total * 100 if b_total > 0 else 0

print(f"\n  {'區間':<20} {'筆數':>5} {'勝率':>7} {'說明'}")
print(f"  {'-'*60}")
print(f"  {'score >= 50':<20} {h_total:>5} {h_wr:>6.1f}%  高分信號")
print(f"  {'score 45-49.9':<20} {l_total:>5} {l_wr:>6.1f}%  邊緣信號（PRIOR=45 可能過濾的）")
print(f"  {'score < 45':<20} {b_total:>5} {b_wr:>6.1f}%  已被過濾的")

if l_wr < h_wr:
    print(f"\n  → 邊緣信號勝率 ({l_wr:.1f}%) < 高分信號 ({h_wr:.1f}%)")
    print(f"    過濾這些信號可以提升整體交易品質")
elif l_total == 0:
    print(f"\n  → 45-50 區間無信號")
else:
    print(f"\n  → 邊緣信號勝率 ({l_wr:.1f}%) >= 高分信號 ({h_wr:.1f}%)")
    print(f"    過濾這些信號可能誤殺好信號，需謹慎")


# ====== 結論 ======
print()
print("=" * 70)
print("6. 結論")
print("=" * 70)

all_pass = [h for h in history if h.get("score", 0) >= 45]
ap_wins = sum(1 for h in all_pass if h.get("result") == "win")
ap_losses = sum(1 for h in all_pass if h.get("result") == "lose")
ap_total = ap_wins + ap_losses
ap_wr = ap_wins / ap_total * 100 if ap_total > 0 else 0

# 假設 PRIOR=45 過濾掉的是 score 45-50 中 n 較小的
# 最保守估計：全部 45-50 都被過濾
after_total = h_total  # only >= 50 remains
after_wr = h_wr

print(f"""
  現狀 (PRIOR=50, score >= 45):
    通過信號: {ap_total} 筆, 勝率: {ap_wr:.1f}%

  PRIOR=45 後（保守估計，45-50 全部被過濾）:
    通過信號: {after_total} 筆, 勝率: {after_wr:.1f}%
    減少信號: {l_total} 筆 ({l_total/ap_total*100:.1f}% of total)

  注意：實際影響取決於每筆信號的 n 值。
  PRIOR=45 只影響 adj_rate 計算，n 越小影響越大。
  n=5 時 adj_rate 下降 100/25 = 4.0 分
  n=10 時 adj_rate 下降 100/30 = 3.3 分
  n=20 時 adj_rate 下降 100/40 = 2.5 分
  n=50 時 adj_rate 下降 100/70 = 1.4 分
""")
