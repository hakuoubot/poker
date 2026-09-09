"""
実現率(equity realization)をソルバーで実測する
=============================================
    python calibrate.py

何のための道具か
----------------
`ranges_gen.py` がプリフロップのレンジ表を作るとき、
「コールされた後、勝率はそのままは実現しない(82%くらい)」という
**当てずっぽうの定数**を使っている。一番よく使う表の土台がこれなので、
実測値に置き換えたい。

ソルバーは EV を出力しないので、EV を読むことはできない。
代わりに **戦略に従って手を最後まで打ち回すシミュレーション** をする。
出力の木には全ノード・全ハンドの行動頻度が入っているので、それに従って
サイコロを振れば、実際に何チップ取れるかが測れる。

実現率の定義
------------
    実現率 = 実際に取れた額 / (素の勝率 x 今のポット)

分母は「そのままショーダウンまでチェックで進んだ場合」。
つまり **チェックダウンすると実現率はちょうど 1.0 になる**。
ベットしたり降りたりすることで、そこから上下する。

同じシミュレーションの中で、同じリバーカードを使って
「もしチェックダウンしていたら勝っていたか」も同時に数えるので、
分子と分母が同じ標本から出る。乱数のブレが打ち消し合って精度が良い。

分かっている限界
----------------
- **フロップからの全ストリートは測れない。** ダンプが 3.3GB になるため
  (実測)。ターンからなら 29MB で収まるので、こちらで測る。
- したがって出るのは「ターン以降の実現率」。プリフロップから使うには
  フロップ→ターンぶんを別に測って掛け合わせる必要がある。
- ソルバーに渡したレンジと実際の相手のレンジが違えば、当然ずれる。
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "solver_engine"))

import solver_bridge as sv                                  # noqa: E402
import strategy as st                                       # noqa: E402
from cards import parse_cards, card_code, cards_code        # noqa: E402
from evaluator import evaluate                              # noqa: E402

# ソルバーの席番号。木の根(先に行動する側)が player 1 なので、
# 1 = OOP(アウトオブポジション)、0 = IP(ポジションあり)。
OOP, IP = 1, 0

# 測る局面。ターンからなら全ストリートをダンプできる(29MB)。
TURN_SPOTS = [
    ("Qs8d7h2c", "BTN", "CO", 6.0, 97.0, "繋がったボード"),
    ("Ah7d2c9s", "BTN", "CO", 6.0, 97.0, "Aハイ・バラバラ"),
    ("Kd7d3d8h", "BTN", "CO", 6.0, 97.0, "3枚同じスート"),
]

# フロップからも測る。全ストリートは 3.3GB になるので、
# フロップ+ターンまでをダンプして(31MB)、リバーはチェックダウン扱いにする。
# リバーで打ち合う分が抜けるぶん、実現率は 1.0 に寄る方に偏る。
FLOP_SPOTS = [
    ("Qs8d7h", "BTN", "BB", 6.0, 97.0, "繋がったボード"),
    ("Ah7d2c", "BTN", "BB", 6.0, 97.0, "Aハイ・バラバラ"),
    ("Qs8d7h", "SB", "BB", 6.0, 97.0, "繋がったボード / 自分がOOP"),
]


def _hand_cards(key):
    """'AsKh' -> カード2枚。"""
    return parse_cards(key)


def _street_commit(action):
    """行動の文字列から、そのストリートでの合計拠出額を取り出す。

    BET も RAISE も「そのストリートでいくら出しているか」の合計で書かれている。
    """
    parts = action.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            return None
    return None


def _pick(rng, actions, freqs):
    total = sum(freqs)
    if total <= 0:
        return actions[0]
    x = rng.random() * total
    for a, f in zip(actions, freqs):
        x -= f
        if x <= 0:
            return a
    return actions[-1]


def playout(tree, board, pot, stack, hands, rng, river_card):
    """戦略に従って1回打ち回す。自分(hero=IP側)の増減を返す。

    hands は {席番号: カード2枚}。river_card は先に決めておいたリバー。
    「もしチェックダウンしていたら」の判定に同じカードを使うため。
    """
    node = tree
    board = list(board)
    invested = {OOP: 0.0, IP: 0.0}       # 局全体でこの局面以降に出した額
    commit = {OOP: 0.0, IP: 0.0}         # 今のストリートでの拠出

    while True:
        kind = node.get("node_type")

        if kind == "chance_node":
            for who in (OOP, IP):
                invested[who] += commit[who]
                pot += commit[who]
                commit[who] = 0.0
            child = (node.get("dealcards") or {}).get(card_code(river_card))
            if not child:
                return None            # そのカードが木に無い(配り済みなど)
            board.append(river_card)
            node = child
            continue

        player = node.get("player")
        strat = node.get("strategy")
        if not strat:
            break                       # ここで終わり(ショーダウン)
        key_hand = hands[player]
        key = sv.hand_key(key_hand)
        table = strat["strategy"]
        if key not in table:
            return None
        action = _pick(rng, strat["actions"], table[key])

        if action == "FOLD":
            for who in (OOP, IP):
                invested[who] += commit[who]
                pot += commit[who]
            winner = OOP if player == IP else IP
            return _result(winner, IP, pot, invested)

        if action == "CHECK":
            pass
        elif action == "CALL":
            commit[player] = max(commit[OOP], commit[IP])
        else:
            amount = _street_commit(action)
            if amount is None:
                return None
            commit[player] = min(amount, stack)

        child = (node.get("childrens") or {}).get(action)
        if not child:
            break                       # 行き止まり = ショーダウン
        node = child

    # ショーダウン
    for who in (OOP, IP):
        invested[who] += commit[who]
        pot += commit[who]
    if len(board) < 5:
        board = board + [river_card]
    a = evaluate(list(hands[IP]) + board)
    b = evaluate(list(hands[OOP]) + board)
    if a > b:
        return _result(IP, IP, pot, invested)
    if b > a:
        return _result(OOP, IP, pot, invested)
    return pot / 2.0 - invested[IP]


def _result(winner, hero, pot, invested):
    return (pot if winner == hero else 0.0) - invested[hero]


def measure(tree, board, pot, stack, iters=60000, seed=1):
    """実現率を測る。全体の値と、**手の強さ別の値**の両方を返す。

    レンジ全体の平均を1つの定数として使うと、弱い手にまで
    「ポジションがあるから素の勝率以上に取れる」を適用してしまい、
    どんなゴミ手でもレイズが得という答えになる(実際にそうなった)。
    実現率は手の強さで大きく変わるので、強さ別に出す必要がある。
    """
    rng = random.Random(seed)
    oop_keys = list(tree["strategy"]["strategy"].keys())

    ip_node = None
    for child in (tree.get("childrens") or {}).values():
        if child.get("strategy"):
            ip_node = child
            break
    ip_keys = list(ip_node["strategy"]["strategy"].keys())

    board_set = set(board)
    net_total = 0.0
    share_total = 0.0
    used = 0
    per_hand = {}          # ハンド -> [取り分の合計, 勝ち分の合計, 回数]

    for _ in range(iters):
        key_ip = ip_keys[rng.randrange(len(ip_keys))]
        h_ip = _hand_cards(key_ip)
        h_oop = _hand_cards(oop_keys[rng.randrange(len(oop_keys))])
        if set(h_ip) & set(h_oop) or set(h_ip) & board_set                 or set(h_oop) & board_set:
            continue
        dead = board_set | set(h_ip) | set(h_oop)
        river = rng.randrange(52)
        while river in dead:
            river = rng.randrange(52)

        net = playout(tree, board, pot, stack,
                      {IP: h_ip, OOP: h_oop}, rng, river)
        if net is None:
            continue

        full = list(board) + [river]
        a = evaluate(list(h_ip) + full)
        b = evaluate(list(h_oop) + full)
        share = 1.0 if a > b else (0.5 if a == b else 0.0)

        net_total += net
        share_total += share
        used += 1
        rec = per_hand.setdefault(key_ip, [0.0, 0.0, 0])
        rec[0] += net
        rec[1] += share
        rec[2] += 1

    if used == 0:
        return None

    eq = share_total / used
    realized = net_total / used
    overall = realized / (eq * pot) if eq * pot else None

    # 強さ別。素の勝率でまとめる
    buckets = [(0.0, 0.30), (0.30, 0.40), (0.40, 0.50),
               (0.50, 0.60), (0.60, 0.75), (0.75, 1.01)]
    curve = []
    for lo, hi in buckets:
        n = w = tot = 0
        for _key, (net_sum, share_sum, cnt) in per_hand.items():
            if cnt < 20:
                continue
            e = share_sum / cnt
            if lo <= e < hi:
                n += cnt
                w += share_sum
                tot += net_sum
        if n == 0:
            curve.append((lo, hi, None, 0))
            continue
        e = w / n
        r = (tot / n) / (e * pot) if e * pot else None
        curve.append((lo, hi, r, n, e, (tot / n) / pot))
    return overall, realized, eq, used, curve


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if sv.find_solver() is None:
        print("ソルバー本体がありません")
        return

    print("実現率を実測します\n")
    for title, spots, rounds in (
            ("ターン以降(リバーまで全部)", TURN_SPOTS, 4),
            ("フロップ以降(リバーはチェックダウン扱い)", FLOP_SPOTS, 2)):
        print("=== %s ===" % title)
        _run(spots, rounds)


def _run(spots, rounds):
    results = []
    for board, hero, villain, pot, stack, label in spots:
        cards = parse_cards(board)
        sit = st.Situation(parse_cards("AsKs"), cards, hero_pos=hero,
                           villain_pos=villain, pot=pot, to_call=0,
                           stack=stack)
        ip_range, oop_range, _isip = st.solver_ranges(sit)
        cmds = sv.build_commands(cards, ip_range, oop_range, pot, stack,
                                 dump_rounds=rounds, out_name="calib.json",
                                 max_iteration=60)
        tree, sec, expl, cached = sv.run_solver(cmds, "calib.json",
                                                timeout=900)
        out = measure(tree, cards, pot, stack)
        if out is None:
            print("%-12s 測定できませんでした" % label)
            continue
        ratio, realized, eq, used, curve = out
        results.append(ratio)
        print("%-14s %s" % (label, cards_code(cards)))
        print("   素の勝率 %.1f%%  そのままなら %.2fBB 取れるはず"
              % (eq * 100, eq * pot))
        print("   実測の取り分 %.2fBB  ->  全体の実現率 %.3f  (%d回, %s)"
              % (realized, ratio, used,
                 "保存から" if cached else "%.0f秒で solve" % sec))
        print("   手の強さ別(比ではなく取り分そのもの):")
        for lo, hi, r, n, e, frac in curve:
            if r is None:
                continue
            print("     素の勝率 %5.1f%% -> 取り分 ポットの %5.1f%% "
                  "(比だと %.2f, %d回)" % (e * 100, frac * 100, r, n))

    if results:
        avg = sum(results) / len(results)
        print("  この組の平均: %.3f\n" % avg)


if __name__ == "__main__":
    main()
