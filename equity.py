"""
勝率(エクイティ)の計算
======================
自分の2枚と場のカードから、ショーダウンまで行ったときに勝つ確率を出す。

- 残りの組み合わせが十分少ないとき(リバー、ターン+相手1人)は全部数え上げる。
  近似ではないので、そのときは exact=True で返す。
- それ以外はモンテカルロ法(ランダムに埋めて多数回試す)。
  試行回数 n のとき勝率の標準誤差はおおよそ 0.5/sqrt(n)。
  2万回で 0.35% 程度なので、実戦の判断には十分。

相手のハンドは「完全ランダム」か「レンジ(取りうる2枚の集合)」のどちらでも指定できる。
ベットに対して降りるか決めるときは、ランダム相手の勝率は自分に甘く出る。
preflop.py のレンジを渡すと現実的な数字になる。

外部通信は一切しない。標準ライブラリのみ。
"""

import random
from itertools import combinations
from math import comb

from cards import FULL_DECK
from evaluator import evaluate


class EquityResult:
    """勝率の計算結果。equity は引き分けの分割を織り込んだ取り分の期待値。"""

    def __init__(self, win, tie, lose, equity, samples, exact):
        self.win = win
        self.tie = tie
        self.lose = lose
        self.equity = equity
        self.samples = samples
        self.exact = exact

    def __repr__(self):
        return ("EquityResult(equity=%.4f win=%.4f tie=%.4f n=%d exact=%s)"
                % (self.equity, self.win, self.tie, self.samples, self.exact))


class Cancelled(Exception):
    """計算の途中打ち切り(GUIで入力が変わったときなど)。"""


def remaining_deck(known):
    used = set(known)
    return [c for c in FULL_DECK if c not in used]


def _exact_cost(deck_size, board_needed):
    """相手1人・ランダムのときの全数え上げの手数。"""
    return comb(deck_size, board_needed) * comb(deck_size - board_needed, 2)


def equity(hole, board=(), opponents=1, opp_range=None, iters=20000,
           seed=None, exact_limit=120000, progress=None, should_stop=None):
    """勝率を計算する。

    hole      : 自分の2枚
    board     : 場の0〜5枚
    opponents : ショーダウンまで残る相手の人数
    opp_range : 相手が持ちうる2枚の組み合わせのリスト。None なら完全ランダム。
                全員に同じレンジを使う。
    iters     : モンテカルロの試行回数(全数え上げできる場合は無視される)
    progress  : 進捗率(0.0〜1.0)を受け取る関数。GUI 用。
    should_stop : True を返したら Cancelled を投げて中断する関数。
    """
    hole = list(hole)
    board = list(board)
    if len(hole) != 2:
        raise ValueError("自分のカードは2枚必要")
    if len(board) > 5:
        raise ValueError("場のカードは5枚まで")
    known = hole + board
    if len(set(known)) != len(known):
        raise ValueError("同じカードが2回使われている")

    deck = remaining_deck(known)
    board_needed = 5 - len(board)

    if (opponents == 1 and opp_range is None
            and _exact_cost(len(deck), board_needed) <= exact_limit):
        return _exact_one_opponent(hole, board, deck, board_needed,
                                   progress, should_stop)

    return _monte_carlo(hole, board, deck, board_needed, opponents,
                        opp_range, iters, seed, progress, should_stop)


def _exact_one_opponent(hole, board, deck, board_needed,
                        progress, should_stop):
    win = tie = lose = 0
    total = _exact_cost(len(deck), board_needed)
    done = 0
    step = max(1, total // 50)

    for extra in combinations(deck, board_needed):
        full_board = board + list(extra)
        hero = evaluate(hole + full_board)
        rest = [c for c in deck if c not in extra]
        for opp in combinations(rest, 2):
            other = evaluate(list(opp) + full_board)
            if hero > other:
                win += 1
            elif hero == other:
                tie += 1
            else:
                lose += 1
            done += 1
            if done % step == 0:
                if should_stop and should_stop():
                    raise Cancelled()
                if progress:
                    progress(done / total)

    n = win + tie + lose
    return EquityResult(win / n, tie / n, lose / n,
                        (win + tie * 0.5) / n, n, True)


def _monte_carlo(hole, board, deck, board_needed, opponents, opp_range,
                 iters, seed, progress, should_stop):
    rng = random.Random(seed)
    sample = rng.sample

    win = tie = 0
    share = 0.0
    done = 0
    step = max(1, iters // 50)
    need = board_needed + 2 * opponents

    # レンジ指定ありのときは、自分のカードと衝突しない組み合わせだけ残す
    pool = None
    if opp_range is not None:
        blocked = set(hole) | set(board)
        pool = [c for c in opp_range
                if c[0] not in blocked and c[1] not in blocked]
        if not pool:
            raise ValueError("そのレンジは自分のカードと全部かぶっている")

    for i in range(iters):
        if opp_range is None:
            drawn = sample(deck, need)
            full_board = board + drawn[:board_needed]
            hands = [drawn[board_needed + 2 * k: board_needed + 2 * k + 2]
                     for k in range(opponents)]
        else:
            hands = []
            used = set(hole) | set(board)
            ok = True
            for _ in range(opponents):
                for _retry in range(40):
                    h = pool[rng.randrange(len(pool))]
                    if h[0] not in used and h[1] not in used:
                        hands.append(list(h))
                        used.add(h[0])
                        used.add(h[1])
                        break
                else:
                    ok = False
                    break
            if not ok:
                continue
            if board_needed:
                rest = [c for c in deck if c not in used]
                full_board = board + sample(rest, board_needed)
            else:
                full_board = board

        hero = evaluate(hole + full_board)
        best = -1
        ties = 0
        for h in hands:
            v = evaluate(h + full_board)
            if v > best:
                best = v
                ties = 0
            elif v == best:
                ties += 1

        if hero > best:
            win += 1
            share += 1.0
        elif hero == best:
            tie += 1
            share += 1.0 / (ties + 2)

        done += 1
        if (i + 1) % step == 0:
            if should_stop and should_stop():
                raise Cancelled()
            if progress:
                progress((i + 1) / iters)

    n = max(done, 1)
    return EquityResult(win / n, tie / n, (n - win - tie) / n,
                        share / n, done, False)


def standard_error(result):
    """勝率のばらつき(標準誤差)。全数え上げなら 0。"""
    if result.exact or result.samples <= 1:
        return 0.0
    p = result.equity
    return (p * (1 - p) / result.samples) ** 0.5
