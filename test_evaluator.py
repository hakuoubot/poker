"""
評価器の検証
============
    python test_evaluator.py

evaluate() の結果を、まったく別実装の「C(7,5)を総当たりする素朴な評価器」と
突き合わせる。役の絶対値ではなく **2つの手の優劣が一致するか** を見る
(引き分けも一致すること)。速い実装のビット演算にミスがあれば必ずここで落ちる。
"""

import itertools
import random
import time

from cards import FULL_DECK, parse_cards, cards_label
from evaluator import evaluate, describe

_RANKS = "23456789TJQKA"


def _naive_five(cards):
    """素朴な5枚評価。(カテゴリ, ランク列) のタプルを返す。"""
    ranks = sorted((c >> 2 for c in cards), reverse=True)
    suits = [c & 3 for c in cards]
    flush = len(set(suits)) == 1

    uniq = sorted(set(ranks), reverse=True)
    straight_high = -1
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:
            straight_high = 3

    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    # 出現数の多い順、同数ならランクの高い順
    grouped = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
    shape = [n for _r, n in grouped]
    ordered = [r for r, _n in grouped]

    if flush and straight_high >= 0:
        return (8, [straight_high])
    if shape[0] == 4:
        return (7, ordered[:2])
    if shape[:2] == [3, 2]:
        return (6, ordered[:2])
    if flush:
        return (5, ranks)
    if straight_high >= 0:
        return (4, [straight_high])
    if shape[0] == 3:
        return (3, ordered[:3])
    if shape[:2] == [2, 2]:
        return (2, ordered[:3])
    if shape[0] == 2:
        return (1, ordered[:4])
    return (0, ranks)


def naive_best(cards):
    return max(_naive_five(c) for c in itertools.combinations(cards, 5))


def _sign(x):
    return (x > 0) - (x < 0)


def check_random(trials=20000, seed=1):
    rng = random.Random(seed)
    deck = list(FULL_DECK)
    bad = 0
    for _ in range(trials):
        rng.shuffle(deck)
        a, b = deck[:7], deck[7:14]
        fast = _sign(evaluate(a) - evaluate(b))
        slow = _sign((naive_best(a) > naive_best(b)) -
                     (naive_best(a) < naive_best(b)))
        if fast != slow:
            bad += 1
            print("[NG]", cards_label(a), "vs", cards_label(b),
                  describe(evaluate(a)), "/", describe(evaluate(b)))
            if bad > 5:
                break
    return bad


def check_known():
    """役の並び順が仕様どおりか、代表的な手で確かめる。"""
    cases = [
        ("As Ks Qs Js Ts 2c 3d", "ロイヤルフラッシュ"),
        ("9s 8s 7s 6s 5s Ac Kd", "ストレートフラッシュ(9ハイ)"),
        ("7c 7d 7h 7s 2c 3d 4h", "フォーカード(7)"),
        ("Kc Kd Kh 4s 4c 2d 9h", "フルハウス(K over 4)"),
        ("Ac Tc 8c 5c 2c Kd Qh", "フラッシュ(Aハイ)"),
        ("Ac 2d 3h 4s 5c 9h Kd", "ストレート(5ハイ)"),
        ("Ac Kd Qh Js Tc 2d 3h", "ストレート(Aハイ)"),
        ("6c 6d 6h Ks 2c 3d 9h", "スリーカード(6)"),
        ("Ac Ad 5h 5s Kc 2d 3h", "ツーペア(A と 5)"),
        ("Jc Jd 9h 5s Kc 2d 3h", "ワンペア(J)"),
        ("Ac Jd 9h 5s Kc 2d 7h", "ハイカード(A)"),
        # スリーカードとフラッシュが同居する手。フラッシュが勝つのが正しい
        # (なお7枚ではフラッシュとフルハウスは同居し得ない。
        #  同スートは各ランク1枚しかなく、3枚+2枚を作ると必ずスートが割れるため)
        ("Ks Qs Js 9s 2s Kc Kd", "フラッシュ(Kハイ)"),
    ]
    bad = 0
    for text, expect in cases:
        got = describe(evaluate(parse_cards(text)))
        mark = "OK " if got == expect else "NG "
        if got != expect:
            bad += 1
        print("  %s%-24s %s" % (mark, text, got))
    return bad


def benchmark(n=100000, seed=7):
    rng = random.Random(seed)
    deck = list(FULL_DECK)
    hands = []
    for _ in range(200):
        rng.shuffle(deck)
        hands.append(deck[:7])
    t0 = time.perf_counter()
    for i in range(n):
        evaluate(hands[i % 200])
    dt = time.perf_counter() - t0
    print("  %d回 %.2f秒 (%.1f万回/秒)" % (n, dt, n / dt / 10000))


if __name__ == "__main__":
    print("代表的な役:")
    bad1 = check_known()
    print("素朴な実装との突き合わせ(2万局面):")
    bad2 = check_random()
    print("  不一致 %d件" % bad2)
    print("速度:")
    benchmark()
    print("結果:", "OK" if bad1 == bad2 == 0 else "NG")
