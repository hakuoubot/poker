"""
169x169 のプリフロップ勝率行列
==============================
    python equity_matrix.py          作って matrix.json に保存する
    python equity_matrix.py --check  保存済みの中身を確認する

Nash 解を反復で求めるには「AKs は 77 に対して何%勝つか」を何万回も引く。
毎回モンテカルロを回していたら終わらないので、**169表記どうしの総当たりを
一度だけ計算して表にする**。

数分かかるが一度作れば使い回せる。`nash.py` はこの表の上で動く。

分かっている近似
----------------
- **ブロッカーを無視している。** 「AKs 対 AA」は本当は自分がAを2枚持つぶん
  相手がAAである組み合わせが減るが、表は169表記どうしの平均で持っている。
  正確にやるには 1326x1326 が要るが、そこまでの精度は要らない。
- モンテカルロなので誤差がある(既定 3000回で ±0.9% 程度)。
  Nash の反復で使う分には、この誤差は解の形をほとんど変えない。
"""

import argparse
import io
import json
import os
import sys
import time
from functools import lru_cache
from itertools import combinations

from cards import RANKS, NUM_CARDS, make_card
from equity import equity
from preflop import hand_class

HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX_PATH = os.path.join(HERE, "equity_matrix.json")

ITERS = 3000
SEED = 20260909


def all_classes():
    """169表記。強い順ではなく、ランクの並び順。"""
    out = []
    for i in range(12, -1, -1):
        for j in range(12, -1, -1):
            if i == j:
                out.append(RANKS[i] * 2)
            elif i > j:
                out.append(RANKS[i] + RANKS[j] + "s")
            else:
                out.append(RANKS[j] + RANKS[i] + "o")
    return sorted(set(out))


@lru_cache(maxsize=None)
def combos_of(cls):
    """その表記に該当するカード2枚の組み合わせ。"""
    out = []
    for a, b in combinations(range(NUM_CARDS), 2):
        if hand_class((a, b)) == cls:
            out.append((a, b))
    return tuple(out)


def _representative(cls):
    hi, lo = RANKS.index(cls[0]), RANKS.index(cls[1])
    if len(cls) == 2:
        return [make_card(hi, 3), make_card(lo, 2)]
    if cls[2] == "s":
        return [make_card(hi, 3), make_card(lo, 3)]
    return [make_card(hi, 3), make_card(lo, 2)]


def _pair_equity(cls_a, cls_b, iters, seed):
    """cls_a が cls_b に対して勝つ割合。カードがかぶる場合は近い形で代用する。"""
    a = _representative(cls_a)
    for b in combos_of(cls_b):
        if not (set(a) & set(b)):
            return equity(a, [], opponents=1, opp_range=[b],
                          iters=iters, seed=seed).equity
    return 0.5           # 同じ表記でカードが必ずかぶる場合(起こらないはず)


def build(iters=ITERS, seed=SEED, progress=True):
    classes = all_classes()
    n = len(classes)
    matrix = {}
    t0 = time.perf_counter()
    done = 0
    total = n * (n + 1) // 2

    for i, ca in enumerate(classes):
        row = matrix.setdefault(ca, {})
        for cb in classes[i:]:
            eq = _pair_equity(ca, cb, iters, seed + done)
            row[cb] = round(eq, 4)
            matrix.setdefault(cb, {})[ca] = round(1.0 - eq, 4)
            done += 1
            if progress and done % 500 == 0:
                el = time.perf_counter() - t0
                print("  %d/%d  %.0f秒経過 (残り %.0f秒見込み)"
                      % (done, total, el, el * (total - done) / done))
    return classes, matrix


def save(classes, matrix, path=MATRIX_PATH):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump({"iters": ITERS, "classes": classes, "matrix": matrix}, f)


def load(path=MATRIX_PATH):
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["classes"], data["matrix"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--iters", type=int, default=ITERS)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.check:
        got = load()
        if got is None:
            print("まだ作られていません")
            return
        classes, matrix = got
        print("%d表記 x %d表記" % (len(classes), len(classes)))
        for a, b in (("AA", "KK"), ("AA", "72o"), ("AKs", "22"),
                     ("JTs", "AQo"), ("55", "AKo")):
            print("  %-4s vs %-4s  %.1f%%" % (a, b, matrix[a][b] * 100))
        return

    print("169x169 の勝率行列を作ります(数分かかります)")
    classes, matrix = build(iters=args.iters)
    save(classes, matrix)
    size = os.path.getsize(MATRIX_PATH) / 1024
    print("equity_matrix.json に保存しました (%.0fKB)" % size)


if __name__ == "__main__":
    main()
