"""
ICM(順位期待値)
================
トーナメントでは、チップが増えた分だけ得をするわけではない。
順位でポイントが決まるので、**チップの期待値ではなく順位ポイントの期待値**で
判断しないといけない。麻雀版の akochan が順位期待値で打牌を選んでいたのと同じ考え方。

ここでやっていること
--------------------
Malmuth-Harville モデル(ICMの標準的なやり方)。
「今のチップ量に比例した確率で1位が決まり、その人を除いて同じことを繰り返す」
と考えて、自分が各順位になる確率を出し、順位ポイントを掛けて足す。

    1位になる確率 = 自分のチップ / 全員のチップ

これは近似で、実力差もポジションもブラインドの上がり方も見ていない。
それでもチップEVよりはるかに実戦に近い(特に終盤)。

ポーカーチェイスのランク戦のポイント
------------------------------------
6人卓のターボトーナメントで、順位ごとにレートが増減する。
STAGE V の実測値を既定にしてある。レート差による補正は入れていない
(自分と場の平均の差 40 につき ±1 程度なので、判断の向きは変わらない)。

単位について
------------
チップ量は BB 単位でも実チップでもよい。ICM は比率しか見ないので同じ結果になる。
アプリは全部 BB 単位で統一している。
"""

# 順位ポイントのプリセット。1位から順に並べる。
POINT_PRESETS = {
    "ポーカーチェイス STAGE V": [35, 21, 7, -6, -19, -28],
    "ポーカーチェイス 基準値": [34, 17, 3, -3, -17, -34],
    "ビギナー(4人卓)": [35, 7, -19, -28],
    "均等(順位を考えない)": None,     # チップEVで判断する
}

DEFAULT_PRESET = "ポーカーチェイス STAGE V"


def finish_probabilities(stacks, hero=0):
    """自分が各順位になる確率。stacks[hero] が自分。

    「まだ決まっていない中から、チップ量に比例した確率で次の1位が決まる」を
    繰り返す。部分集合ごとの確率を一度だけ計算して使い回す
    (6人なら64通りなので一瞬)。チップが0以下の人は既に飛んだ扱い。
    """
    alive = [i for i, s in enumerate(stacks) if s > 0]
    probs = [0.0] * max(len(stacks), 1)
    if hero not in alive:
        probs[len(alive)] = 1.0        # 飛んでいる人は生き残りの1つ下
        return probs

    live = [stacks[i] for i in alive]
    hero_pos = alive.index(hero)
    n = len(live)
    total = sum(live)

    # taken[S] = 集合 S の人たちが上位 |S| 位を(順番は問わず)占める確率
    taken = [0.0] * (1 << n)
    taken[0] = 1.0
    for mask in range(1 << n):
        if taken[mask] <= 0:
            continue
        left = total - sum(live[i] for i in range(n) if mask >> i & 1)
        if left <= 0:
            continue
        for i in range(n):
            if mask >> i & 1:
                continue
            taken[mask | (1 << i)] += taken[mask] * live[i] / left

    for mask in range(1 << n):
        if mask >> hero_pos & 1 or taken[mask] <= 0:
            continue
        left = total - sum(live[i] for i in range(n) if mask >> i & 1)
        if left <= 0:
            continue
        place = bin(mask).count("1")
        probs[place] += taken[mask] * live[hero_pos] / left
    return probs


def hero_points(stacks, points, hero=0):
    """自分の順位期待ポイント。

    points は1位から順のポイント。人数より短ければ足りない分は最下位の値を使う。
    points が None のときはチップEV(チップ量そのもの)を返す。
    """
    if points is None:
        return float(stacks[hero])
    probs = finish_probabilities(stacks, hero)
    total = 0.0
    for k, p in enumerate(probs):
        if p <= 0:
            continue
        total += p * (points[k] if k < len(points) else points[-1])
    return total


def required_equity(win_stacks, lose_stacks, fold_stacks, points, hero=0):
    """コールが降りると釣り合う勝率。

    EV(コール) = eq x ICM(勝ち) + (1-eq) x ICM(負け)
    これが ICM(降りる) と等しくなる eq を返す。
    チップ基準のポットオッズより高くなるのが普通で、その差がICMの効き目。
    """
    w = hero_points(win_stacks, points, hero)
    l = hero_points(lose_stacks, points, hero)
    f = hero_points(fold_stacks, points, hero)
    if abs(w - l) < 1e-12:
        return None
    return (f - l) / (w - l)


def confrontation_stacks(stacks, hero, villain, pot, hero_total, call_amount):
    """自分と相手が1対1で決着したときのチップ量。

    stacks      : この勝負に入る前の各自の残り(ポットに入れた分は含まない)
    pot         : 相手のベットを含めた、いま場にあるチップ
    hero_total  : 自分がこれから出す合計(コールなら call_amount、レイズならその額)
    call_amount : 相手のベット額(自分が払わされている額)

    戻り値は (勝ち, 負け, 相手が降りる, 自分が降りる) の4通り。
    チップの総量はどれも変わらない。
    """
    s_h, s_v = stacks[hero], stacks[villain]
    hero_total = max(0.0, min(hero_total, s_h))
    extra = max(0.0, hero_total - call_amount)      # 相手が追加で払う額
    extra = min(extra, s_v)

    win = list(stacks)
    win[hero] = s_h + pot + extra
    win[villain] = s_v - extra

    lose = list(stacks)
    lose[hero] = s_h - hero_total
    lose[villain] = s_v + pot + hero_total

    villain_folds = list(stacks)
    villain_folds[hero] = s_h + pot

    hero_folds = list(stacks)
    hero_folds[villain] = s_v + pot

    return win, lose, villain_folds, hero_folds


def multi_outcomes(stacks, hero, villains, pot, hero_total, call_amount):
    """複数人相手で決着したときのチップ量。

    2人用の confrontation_stacks() の一般形。マルチウェイのポットは
    6人卓では普通に起きるが、勝負相手を1人に固定すると
    「誰にいくら渡ったか」がずれて順位EVが狂う。

    戻り値は (勝ち, [相手iが勝った場合...], 自分が降りる)。
    自分が負けたときに誰が取るかは分からないので、
    呼び出し側で全員ぶんを平均する(全員が同じ確率で取るとみなす近似)。
    """
    s_h = stacks[hero]
    hero_total = max(0.0, min(hero_total, s_h))
    extras = []
    for v in villains:
        e = max(0.0, hero_total - call_amount)
        extras.append(min(e, stacks[v]))
    added = sum(extras)

    win = list(stacks)
    win[hero] = s_h + pot + added
    for v, e in zip(villains, extras):
        win[v] = stacks[v] - e

    loses = []
    for j, vj in enumerate(villains):
        st = list(stacks)
        st[hero] = s_h - hero_total
        for v, e in zip(villains, extras):
            st[v] = stacks[v] - e
        st[vj] = stacks[vj] - extras[j] + pot + hero_total + added
        loses.append(st)

    hero_folds = list(stacks)
    # 自分が降りた分のポットは、残った相手のうち誰かが取る(均等とみなす)
    share = pot / max(len(villains), 1)
    for v in villains:
        hero_folds[v] = stacks[v] + share

    return win, loses, hero_folds


def after_blinds(stacks, cost, hero=0):
    """1周ぶんのブラインドとアンティを払った後のチップ量。

    ICM には「時間」が無い。降りて待っていても実際にはブラインドで削られるので、
    それを入れないとターボでは降りる判断が甘く出る。

    自分が1周で払う分だけを引き、そのチップは他の生き残りに均等に配る
    (他家同士のやりとりは相殺されるとみなす近似)。
    ICM は非線形なので、同じ 2BB でも短いスタックほど強く効く。
    """
    if cost <= 0:
        return list(stacks)
    out = list(stacks)
    pay = min(cost, out[hero])
    out[hero] -= pay
    others = [i for i, v in enumerate(out) if i != hero and v > 0]
    if others:
        share = pay / len(others)
        for i in others:
            out[i] += share
    return out


def describe(stacks, points, hero=0):
    """順位確率を人に読める形で。"""
    probs = finish_probabilities(stacks, hero)
    parts = []
    for k, p in enumerate(probs):
        if p > 0.005:
            parts.append("%d位 %.0f%%" % (k + 1, p * 100))
    return " / ".join(parts)
