"""
Bayesian Shrinkage 結構性診斷

驗證問題：在現有參數下 (MIN_SIG=5, BAYES_K=20, BAYES_PRIOR=50)，
小樣本信號是否「結構性地」無法被 min_score=45 過濾？

不依賴歷史數據 — 純數學分析。
"""

# === 現有參數 ===
MIN_SIG = 5
BAYES_K = 20
BAYES_PRIOR = 50.0
MIN_SCORE = 45

def adj_rate(raw_wr, n, prior=BAYES_PRIOR, k=BAYES_K):
    """Bayesian shrinkage: adj = prior + (raw - prior) * n/(n+k)"""
    if n < MIN_SIG:
        return None  # 不產生信號
    return prior + (raw_wr - prior) * n / (n + k)


def score(adj_r, weight=1.0, global_penalty=1.0, regime_adj=1.0):
    return adj_r * weight * global_penalty * regime_adj


print("=" * 70)
print("1. adj_rate 在不同 (n, raw_wr) 下的值")
print("=" * 70)
print(f"{'n':>4} | ", end="")
for wr in [20, 30, 40, 50, 60, 70, 80]:
    print(f"wr={wr}%  ", end="")
print()
print("-" * 70)

for n in [5, 7, 10, 15, 20, 30, 50]:
    print(f"{n:>4} | ", end="")
    for wr in [20, 30, 40, 50, 60, 70, 80]:
        ar = adj_rate(wr, n)
        marker = " *" if ar and ar >= MIN_SCORE else "  "
        print(f"{ar:5.1f}{marker} ", end="") if ar else print("  N/A  ", end="")
    print()

print(f"\n  * = adj_rate >= {MIN_SCORE} (過門檻)")
print(f"  注意：score = adj_rate × weight × global_penalty × regime_adj")
print(f"  當三個乘數都為 1.0 時，adj_rate >= 45 即通過")


print("\n" + "=" * 70)
print("2. 關鍵問題：raw_wr 需要低到多少才能讓 score < 45？")
print("=" * 70)

for n in [5, 7, 10, 15, 20, 30]:
    # 解方程：prior + (wr - prior) * n/(n+k) = 45
    # 45 = 50 + (wr - 50) * n/(n+k)
    # -5 = (wr - 50) * n/(n+k)
    # wr - 50 = -5 * (n+k)/n
    # wr = 50 - 5*(n+k)/n
    threshold_wr = 50 - 5 * (n + BAYES_K) / n
    print(f"  n={n:>2}: raw_wr 需 < {threshold_wr:5.1f}% 才被過濾", end="")
    if n == 5:
        print(f"  ← 即 5 次中最多贏 {int(threshold_wr/100*5)} 次 = {int(threshold_wr/100*5)}勝{5-int(threshold_wr/100*5)}敗")
    elif n == 10:
        print(f"  ← 即 10 次中最多贏 {int(threshold_wr/100*10)} 次")
    else:
        print()


print("\n" + "=" * 70)
print("3. 實際場景模擬：暴跌幣的死貓反彈")
print("=" * 70)
print("""
假設 TNSR（-28.85%）在 500 根 1M K 線中：
- 前 400 根（暴跌前）：正常行情
- 後 100 根（暴跌中）：持續下跌 + 死貓反彈

追多觸發條件：連漲2根 + RSI 60-80 + MACD up + OBV up
在 500 根中，追多可能觸發 8-15 次（正常行情7-12次 + 死貓反彈1-3次）
""")

scenarios = [
    ("樂觀：12次觸發, 8勝4敗", 12, 66.7),
    ("中等：8次觸發, 5勝3敗", 8, 62.5),
    ("悲觀：6次觸發, 3勝3敗", 6, 50.0),
    ("最差：5次觸發, 2勝3敗", 5, 40.0),
]

for label, n, wr in scenarios:
    ar = adj_rate(wr, n)
    s = score(ar)
    s_with_regime = score(ar, regime_adj=0.85)
    verdict = "PASS" if s >= MIN_SCORE else "BLOCK"
    verdict_r = "PASS" if s_with_regime >= MIN_SCORE else "BLOCK"
    print(f"  {label}")
    print(f"    adj_rate={ar:.1f}, score={s:.1f} [{verdict}], "
          f"score(regime=0.85)={s_with_regime:.1f} [{verdict_r}]")


print("\n" + "=" * 70)
print("4. 不同參數組合的影響（不改公式結構，只調參數）")
print("=" * 70)

param_sets = [
    ("現狀",           5,  20, 50.0),
    ("A: MIN_SIG=10",  10, 20, 50.0),
    ("B: BAYES_K=40",  5,  40, 50.0),
    ("C: PRIOR=45",    5,  20, 45.0),
    ("D: A+B+C 全調",  10, 40, 45.0),
]

# 測試場景：n=5~10, raw_wr=40~65%
test_cases = [
    (5, 65, "5次觸發,65%勝率"),
    (5, 50, "5次觸發,50%勝率"),
    (7, 57, "7次觸發,57%勝率"),
    (10, 50, "10次觸發,50%勝率"),
    (10, 60, "10次觸發,60%勝率"),
    (20, 55, "20次觸發,55%勝率"),
]

for label, min_sig, k, prior in param_sets:
    print(f"\n  {label} (MIN_SIG={min_sig}, K={k}, PRIOR={prior})")
    print(f"  {'場景':<25} {'adj_rate':>8} {'score':>6} {'結果':>4}")
    print(f"  {'-'*50}")
    for n, wr, desc in test_cases:
        if n < min_sig:
            print(f"  {desc:<25} {'N/A':>8} {'N/A':>6} {'SKIP':>4}")
            continue
        ar = prior + (wr - prior) * n / (n + k)
        s = ar * 1.0 * 1.0 * 1.0  # all multipliers = 1
        v = "PASS" if s >= MIN_SCORE else "BLOCK"
        print(f"  {desc:<25} {ar:>8.1f} {s:>6.1f} {v:>5}")


print("\n" + "=" * 70)
print("5. 統計顯著性分析：n=5 時勝率的置信區間")
print("=" * 70)

from math import comb

print("\n  n=5 時各勝率的二項分布概率：")
print(f"  {'wins':>4} {'raw_wr':>7} {'adj_rate':>9} {'score':>6} {'P(X=wins)':>10} {'結果':>4}")

for wins in range(6):
    wr = wins / 5 * 100
    ar = adj_rate(wr, 5)
    s = score(ar) if ar else 0
    # P(X=wins) assuming true p=0.5 (random)
    prob = comb(5, wins) * (0.5 ** 5)
    v = "PASS" if s >= MIN_SCORE else "BLOCK"
    print(f"  {wins:>4} {wr:>6.1f}% {ar:>8.1f} {s:>6.1f} {prob:>9.3f} {v:>5}")

# 累積概率
pass_prob = sum(comb(5, w) * (0.5**5) for w in range(5+1)
                if adj_rate(w/5*100, 5) >= MIN_SCORE)
print(f"\n  → 即使真實勝率=50%（隨機），n=5 時有 {pass_prob*100:.1f}% 概率通過 min_score=45")
print(f"  → 這意味著 Bayesian shrinkage 在 n=5 時幾乎無法過濾任何策略")


print("\n" + "=" * 70)
print("6. 結論")
print("=" * 70)
print(f"""
  現狀問題：
  - MIN_SIG=5, BAYES_K=20, PRIOR=50 的組合下
  - adj_rate 的下限 = 50 + (0-50)*5/25 = 40（即使全輸）
  - 全輸 (0%) 的 score = 40 × 1.0 × 1.0 × 1.0 = 40 < 45 → BLOCK
  - 但只要 1勝4敗 (20%) → adj_rate = 50 + (20-50)*5/25 = 44 → 差 1 分就過
  - 2勝3敗 (40%) → adj_rate = 48 → PASS

  這是純數學問題：
  - n=5 時，shrinkage 係數 = 5/25 = 0.2，80% 的信息來自 prior=50
  - min_score=45 距離 prior=50 只差 5 分
  - 觀測值只需從 50 偏離 5/0.2 = 25 個百分點（即 wr < 25%）才被過濾
  - 5 次實驗中要 0 勝或 1 勝才會被過濾 — 這需要真實勝率極低

  建議驗證的參數組合（用回測數據測試後再決定）：
  - MIN_SIG=10: 最小改動，效果溫和
  - BAYES_K=40: 加強收縮，但可能影響大樣本策略
  - PRIOR=45: 最精確，將 prior 對齊 min_score

  最穩健的單一改動：PRIOR 從 50 降至 45
  理由：prior=50 意味著「無信息時假設勝率50%」，
        但 min_score=45 意味著「勝率45%以下不值得交易」
        兩者之間的 5 分 gap 讓小樣本策略自動通過
        PRIOR=45 消除這個 gap，讓無信息狀態恰好在門檻上
""")
