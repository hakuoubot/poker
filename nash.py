"""
ICM プッシュ/フォールドの Nash 解を求める
========================================
    python nash.py                10BB の卓を解く(順位ポイント基準)
    python nash.py --chip         チップEV基準で解く(公表されている表と比較用)
    python nash.py --stacks 8,12,20,15,10,25

いままで `pushfold.py` が使っていた相手のコールレンジは、**手で書いた固定表**
だった。ここを **解いた均衡** に置き換えるためのソルバー。
ICMIZER や HRC がやっているのと同じ計算を自前でやる。

何を解いているか
----------------
「誰も参加していないところから、オールインするか降りるか」だけのゲーム。
ターボで浅くなるとほぼこの形になるので、実戦の大半をこれで説明できる。

- 各席が「どのハンドでオールインするか」
- 後ろの各席が「そのオールインにどのハンドでコールするか」

架空プレイ(fictitious play)で解く
---------------------------------
最初は「相手の戦略に対する最善手をそのまま採用する」を繰り返す実装にしたが、
**振動して収束しなかった**(UTG 39%% > HJ 28%% のように順序が逆の解が出た。
押す側が広げると受ける側が広げ、すると押す側が狭め…が延々と続く)。

そこで最良応答をそのまま入れ替えず、**それまでの平均に少しずつ混ぜる**方式に
した。混ぜる割合を 1/(t+2) と小さくしていくと、平均が均衡に寄っていく。

速度について
------------
順位ポイントの値は「誰と誰がぶつかってどちらが勝ったか」だけで決まり、
**手札が何かには依存しない**。169表記のループの内側で計算すると同じ値を
何万回も作り直すことになるので、先に出しておく(`_outcomes`)。

分かっている近似
----------------
- **コールは1人まで**。2人目のオーバーコールは考えない。
- **ブロッカーを無視**。勝率は169表記どうしの平均(equity_matrix.py)。
- **最初のオールイン以外は考えない**。リンプもミニレイズも無い。
  浅い場面ではほぼ問題にならないが、20BBを超えると実態から離れる。
"""

import argparse
import sys

import icm
from equity_matrix import load
from preflop import CLASS_COMBOS

# 席の並び(行動する順)
ORDER = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
TOTAL_COMBOS = 1326.0


def combo_weight(cls):
    return len(CLASS_COMBOS.get(cls, ()))


class Table:
    """卓の状態。スタックはすべて BB 単位。"""

    def __init__(self, stacks, points=None, ante=0.1, sb=0.5, bb=1.0):
        self.stacks = dict(zip(ORDER, stacks))
        self.points = points
        self.ante = ante
        self.sb = sb
        self.bb = bb

    def posted(self):
        """ブラインドとアンティを払った後のスタック。"""
        out = {}
        for pos in ORDER:
            s = self.stacks[pos] - self.ante
            if pos == "SB":
                s -= self.sb
            elif pos == "BB":
                s -= self.bb
            out[pos] = max(s, 0.0)
        return out

    def dead_pot(self):
        return self.sb + self.bb + self.ante * len(ORDER)

    def points_of(self, stacks_by_pos, hero):
        """順位ポイント(points=None ならチップそのもの)。"""
        order = [hero] + [p for p in ORDER if p != hero]
        return icm.hero_points([stacks_by_pos[p] for p in order],
                               self.points, 0)


def _range_weight(strategy):
    """戦略(表記 -> 押す確率)の重み。組み合わせ数で重み付けした割合。"""
    total = 0.0
    for cls, p in strategy.items():
        if p > 0:
            total += combo_weight(cls) * p
    return total / TOTAL_COMBOS


def _equity_vs(matrix, cls, strategy):
    """cls が、混ざった戦略 strategy に対して勝つ割合。"""
    row = matrix[cls]
    total = 0.0
    weight = 0.0
    for c, p in strategy.items():
        if p <= 0:
            continue
        w = combo_weight(c) * p
        total += row[c] * w
        weight += w
    return total / weight if weight else 0.5


# ---------------------------------------------------------
# 順位ポイントの事前計算(手札に依存しないので外に出す)
# ---------------------------------------------------------
def _outcomes(table, shover, caller):
    posted = table.posted()
    pot = table.dead_pot()
    eff = min(posted[shover], posted[caller])

    win = dict(posted)
    win[shover] = posted[shover] + pot + eff
    win[caller] = posted[caller] - eff

    lose = dict(posted)
    lose[shover] = posted[shover] - eff
    lose[caller] = posted[caller] + pot + eff

    stolen = dict(posted)
    stolen[shover] = posted[shover] + pot

    return {
        "shover_win": table.points_of(win, shover),
        "shover_lose": table.points_of(lose, shover),
        "caller_win": table.points_of(lose, caller),
        "caller_lose": table.points_of(win, caller),
        "caller_fold": table.points_of(stolen, caller),
    }


def _steal_points(table, shover):
    posted = table.posted()
    won = dict(posted)
    won[shover] = posted[shover] + table.dead_pot()
    return table.points_of(won, shover)


def _fold_points(table, shover):
    posted = table.posted()
    behind = ORDER[ORDER.index(shover) + 1:]
    folded = dict(posted)
    share = table.dead_pot() / max(len(behind), 1)
    for j in behind:
        folded[j] = posted[j] + share
    return table.points_of(folded, shover)


# ---------------------------------------------------------
# 求解
# ---------------------------------------------------------
def solve(table, rounds=80, verbose=False, tol=0.05):
    got = load()
    if got is None:
        raise RuntimeError("equity_matrix.json がありません。"
                           "先に python equity_matrix.py を実行してください")
    classes, matrix = got

    shove = {p: {c: 0.5 for c in classes} for p in ORDER[:-1]}
    call = {}
    pairs = []
    for i, s_pos in enumerate(ORDER[:-1]):
        for j in ORDER[i + 1:]:
            call[(s_pos, j)] = {c: 0.5 for c in classes}
            pairs.append((s_pos, j))

    out = {pair: _outcomes(table, *pair) for pair in pairs}
    steal = {p: _steal_points(table, p) for p in ORDER[:-1]}
    fold_pt = {p: _fold_points(table, p) for p in ORDER[:-1]}

    for step in range(rounds):
        alpha = 1.0 / (step + 2)
        moved = 0.0

        for (s_pos, j) in pairs:
            o = out[(s_pos, j)]
            strat = shove[s_pos]
            target = call[(s_pos, j)]
            for cls in classes:
                eq = _equity_vs(matrix, cls, strat)
                value = eq * o["caller_win"] + (1 - eq) * o["caller_lose"]
                br = 1.0 if value > o["caller_fold"] else 0.0
                delta = alpha * (br - target[cls])
                target[cls] += delta
                moved += abs(delta)

        for s_pos in ORDER[:-1]:
            behind = ORDER[ORDER.index(s_pos) + 1:]
            weights = [(j, _range_weight(call[(s_pos, j)])) for j in behind]
            target = shove[s_pos]
            for cls in classes:
                reach = 1.0
                value = 0.0
                for j, w in weights:
                    if w > 0:
                        o = out[(s_pos, j)]
                        eq = _equity_vs(matrix, cls, call[(s_pos, j)])
                        value += reach * w * (
                            eq * o["shover_win"]
                            + (1 - eq) * o["shover_lose"])
                    reach *= (1.0 - w)
                value += reach * steal[s_pos]
                br = 1.0 if value > fold_pt[s_pos] else 0.0
                delta = alpha * (br - target[cls])
                target[cls] += delta
                moved += abs(delta)

        if verbose and (step == 0 or step % 10 == 9):
            print("  %3d回目: 変化量 %6.2f  押す割合 %s"
                  % (step + 1, moved,
                     " ".join("%s %.0f%%" % (p, _range_weight(shove[p]) * 100)
                              for p in ORDER[:-1])))
        if moved < tol:
            break
    return {"shove": shove, "call": call}


def range_percent(strategy):
    return _range_weight(strategy) * 100


def to_set(strategy, threshold=0.5):
    """混ざった戦略を「押す表記の集合」にする。既存コードとの橋渡し用。"""
    return {c for c, p in strategy.items() if p >= threshold}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stacks", default="10,10,10,10,10,10")
    ap.add_argument("--chip", action="store_true",
                    help="チップEVで解く(公表値との比較用)")
    ap.add_argument("--ante", type=float, default=0.1)
    ap.add_argument("--rounds", type=int, default=80)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    stacks = [float(x) for x in args.stacks.split(",")]
    points = None if args.chip else icm.POINT_PRESETS[icm.DEFAULT_PRESET]
    table = Table(stacks, points=points, ante=args.ante)

    print("スタック %s / %s"
          % (args.stacks, "チップEV" if args.chip else "順位ポイント(ICM)"))
    result = solve(table, rounds=args.rounds, verbose=True)

    print("\nオールインするレンジ:")
    for p in ORDER[:-1]:
        print("  %-4s %5.1f%%" % (p, range_percent(result["shove"][p])))
    print("\nコールするレンジ(押す席 -> 受ける席):")
    for (s, j) in sorted(result["call"]):
        print("  %-4s -> %-4s %5.1f%%"
              % (s, j, range_percent(result["call"][(s, j)])))


if __name__ == "__main__":
    main()


# ---------------------------------------------------------
# 解いた結果の保存
#
# 1卓解くのに数十秒かかるので、同じ卓構成なら使い回す。
# キーはスタック・順位ポイント・アンティ。
# ---------------------------------------------------------
import hashlib                                              # noqa: E402
import json                                                 # noqa: E402
import os                                                   # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "nash_cache")
_MEMORY = {}


def _key(table, rounds):
    raw = json.dumps({
        "stacks": [round(table.stacks[p], 2) for p in ORDER],
        "points": table.points,
        "ante": table.ante, "sb": table.sb, "bb": table.bb,
        "rounds": rounds,
    }, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def solve_cached(table, rounds=80):
    """solve() と同じだが、同じ卓構成なら保存した結果を返す。"""
    key = _key(table, rounds)
    if key in _MEMORY:
        return _MEMORY[key]

    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            result = {
                "shove": data["shove"],
                "call": {tuple(k.split("|")): v
                         for k, v in data["call"].items()},
            }
            _MEMORY[key] = result
            return result
        except (ValueError, KeyError):
            os.remove(path)

    result = solve(table, rounds=rounds)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"shove": result["shove"],
                   "call": {"%s|%s" % k: v
                            for k, v in result["call"].items()}}, f)
    _MEMORY[key] = result
    return result


def calling_ranges_against(table, shover, rounds=80):
    """その席がオールインしたときに、後ろの各席がコールしてくる範囲。"""
    result = solve_cached(table, rounds=rounds)
    out = {}
    for j in ORDER[ORDER.index(shover) + 1:]:
        strat = result["call"].get((shover, j))
        if strat:
            out[j] = strat
    return out


def push_value(table, cls, shover, rounds=80, floor_range=None):
    """その席がその手でオールインしたときの値。(オールイン, 降りる, 全員降りる確率)。

    floor_range を渡すと、相手のコールレンジを **解いた値とそれの広い方** に
    する。均衡は「相手も均衡通りに降りる」前提で極端に広い押しを出すので、
    実戦向けには相手が降りすぎない前提で評価した方が安全。
    """
    got = load()
    if got is None:
        raise RuntimeError("equity_matrix.json がありません")
    _classes, matrix = got
    result = solve_cached(table, rounds=rounds)

    floor = None
    if floor_range:
        from preflop import expand
        wanted = expand(floor_range)
        floor = {c: (1.0 if c in wanted else 0.0) for c in _classes}

    behind = ORDER[ORDER.index(shover) + 1:]
    ranges = {}
    for j in behind:
        solved = result["call"][(shover, j)]
        if floor is not None and _range_weight(floor) > _range_weight(solved):
            ranges[j] = floor
        else:
            ranges[j] = solved
    weights = [(j, _range_weight(ranges[j])) for j in behind]

    reach = 1.0
    value = 0.0
    for j, w in weights:
        if w > 0:
            o = _outcomes(table, shover, j)
            eq = _equity_vs(matrix, cls, ranges[j])
            value += reach * w * (eq * o["shover_win"]
                                  + (1 - eq) * o["shover_lose"])
        reach *= (1.0 - w)
    value += reach * _steal_points(table, shover)
    return value, _fold_points(table, shover), reach


def table_from_situation(sit):
    """strategy.Situation から Table を作る。

    Situation は [自分, 戦う相手, その他...] の順でスタックを持っているので、
    席に割り当て直す。自分と相手は指定の席、残りは空いている席に順に置く。
    """
    stacks = {}
    stacks[sit.hero_pos] = sit.stack
    if sit.villain_pos != sit.hero_pos:
        stacks[sit.villain_pos] = sit.villain_stack
    rest = [p for p in ORDER if p not in stacks]
    for pos, s in zip(rest, list(sit.other_stacks) + [sit.stack] * len(rest)):
        stacks[pos] = s
    return Table([stacks[p] for p in ORDER], points=sit.points,
                 ante=sit.ante)
