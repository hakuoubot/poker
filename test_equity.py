"""
勝率計算の検証
==============
    python test_equity.py

2段構えで確かめる。

1. モンテカルロと全数え上げを同じ局面でつき合わせる。
   全数え上げは近似なしの真値なので、MC の実装ミスはここで出る。
2. プリフロップの勝率を、広く公表されている数値と比べる
   (ポーカーの本や各種計算機でほぼ確定している値。許容差は1%)。
"""

import time

from cards import parse_cards
from equity import equity, standard_error

# (自分, 場, 説明) 全数え上げが効く局面
EXACT_SPOTS = [
    ("AsAh", "Kd7c2s3d", "AA トップセット、ターン"),
    ("JsTs", "9s8d2c4h", "JTs オープンエンド、ターン"),
    ("AsKd", "Qh7c2s9d", "AK ノーペア、ターン"),
]

# (自分, 相手人数, 公表値, 説明) プリフロップ、相手は完全ランダム
PREFLOP = [
    ("AsAh", 1, 0.852, "AA vs ランダム1人"),
    ("KsKh", 1, 0.824, "KK vs ランダム1人"),
    ("7c2d", 1, 0.346, "72o vs ランダム1人(最弱)"),
    ("AsKs", 1, 0.670, "AKs vs ランダム1人"),
    ("AsAh", 5, 0.494, "AA vs ランダム5人"),
]


def main():
    bad = 0

    print("1. モンテカルロ vs 全数え上げ(同じ局面):")
    for hole, board, note in EXACT_SPOTS:
        h, b = parse_cards(hole), parse_cards(board)
        t0 = time.perf_counter()
        ex = equity(h, b, opponents=1)
        t_ex = time.perf_counter() - t0
        mc = equity(h, b, opponents=1, iters=60000, seed=5,
                    exact_limit=0)
        se = standard_error(mc)
        diff = abs(ex.equity - mc.equity)
        ok = ex.exact and diff <= max(3 * se, 0.004)
        if not ok:
            bad += 1
        print("  %s%-26s 真値 %.4f / MC %.4f (差 %.4f, 3SE %.4f, 数え上げ%d通り %.1f秒)"
              % ("OK " if ok else "NG ", note, ex.equity, mc.equity,
                 diff, 3 * se, ex.samples, t_ex))

    print("2. プリフロップの公表値との比較(MC 6万回):")
    for hole, opp, expect, note in PREFLOP:
        t0 = time.perf_counter()
        r = equity(parse_cards(hole), opponents=opp, iters=60000, seed=42)
        dt = time.perf_counter() - t0
        diff = abs(r.equity - expect)
        ok = diff <= 0.010
        if not ok:
            bad += 1
        print("  %s%-26s 実測 %.3f / 公表値 %.3f (差 %.3f, %.1f秒)"
              % ("OK " if ok else "NG ", note, r.equity, expect, diff, dt))

    print("3. リバーの全数え上げ:")
    t0 = time.perf_counter()
    r = equity(parse_cards("AsAh"), parse_cards("Kd7c2s3d4h"), opponents=1)
    print("  AA vs ランダム1人: %.4f (%d通り, %.2f秒)"
          % (r.equity, r.samples, time.perf_counter() - t0))

    print("結果:", "OK" if bad == 0 else "NG %d件" % bad)


if __name__ == "__main__":
    main()
