"""
ハンド評価器(5〜7枚から役の強さを整数で返す)
=============================================
モンテカルロ法で勝率を出すため、1回の評価が数十マイクロ秒で終わることを
最優先にしている。C(7,5)=21 通りを総当たりする素直な実装ではなく、
ランクの出現数とスート別のビットマスクから直接役を判定している。

`evaluate()` が返すのは「大きいほど強い」整数。
上位ビットに役のカテゴリ、下位にキッカー等のランクが詰めてある。
同じカテゴリなら必ず同じ個数のランクを詰めるので、単純な大小比較で
正しく優劣が決まる(引き分けは値が完全一致する)。
"""

# 役のカテゴリ(大きいほど強い)
HIGH_CARD = 0
ONE_PAIR = 1
TWO_PAIR = 2
THREE_KIND = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
FOUR_KIND = 7
STRAIGHT_FLUSH = 8

CATEGORY_JA = {
    HIGH_CARD: "ハイカード",
    ONE_PAIR: "ワンペア",
    TWO_PAIR: "ツーペア",
    THREE_KIND: "スリーカード",
    STRAIGHT: "ストレート",
    FLUSH: "フラッシュ",
    FULL_HOUSE: "フルハウス",
    FOUR_KIND: "フォーカード",
    STRAIGHT_FLUSH: "ストレートフラッシュ",
}

_RANK_CHAR = "23456789TJQKA"


def _build_straight_table():
    """13bit のランクマスク -> ストレートの最高位ランク(無ければ -1)。

    A-5(ホイール)は A を 5 の下に読むので、最高位は 5(インデックス3)。
    """
    patterns = []
    for high in range(12, 3, -1):
        mask = 0
        for i in range(5):
            mask |= 1 << (high - i)
        patterns.append((mask, high))
    wheel = (1 << 12) | (1 << 3) | (1 << 2) | (1 << 1) | 1
    patterns.append((wheel, 3))

    table = [-1] * (1 << 13)
    for m in range(1 << 13):
        for mask, high in patterns:
            if m & mask == mask:
                table[m] = high
                break
    return table


_STRAIGHT = _build_straight_table()

_CATEGORY_SHIFT = 1 << 24


def _pack(category, ranks):
    v = 0
    for r in ranks:
        v = (v << 4) | r
    return category * _CATEGORY_SHIFT + v


def _top_ranks(mask, n):
    out = []
    for r in range(12, -1, -1):
        if mask >> r & 1:
            out.append(r)
            if len(out) == n:
                break
    return out


def evaluate(cards):
    """カード5〜7枚(内部表現の整数)から役の強さを整数で返す。"""
    rank_count = [0] * 13
    suit_mask = [0, 0, 0, 0]
    suit_count = [0, 0, 0, 0]
    mask = 0
    for c in cards:
        r = c >> 2
        s = c & 3
        rank_count[r] += 1
        suit_count[s] += 1
        suit_mask[s] |= 1 << r
        mask |= 1 << r

    best = -1

    # --- フラッシュ系 ---
    for s in range(4):
        if suit_count[s] >= 5:
            fmask = suit_mask[s]
            sf = _STRAIGHT[fmask]
            if sf >= 0:
                # ストレートフラッシュはこれ以上強い役が同居し得ない
                return _pack(STRAIGHT_FLUSH, [sf])
            best = _pack(FLUSH, _top_ranks(fmask, 5))
            break  # 5枚以上そろうスートは2つ以上あり得ない

    # --- ランクの重なりで決まる役 ---
    quads = []
    trips = []
    pairs = []
    for r in range(12, -1, -1):
        n = rank_count[r]
        if n == 4:
            quads.append(r)
        elif n == 3:
            trips.append(r)
        elif n == 2:
            pairs.append(r)

    if quads:
        q = quads[0]
        kicker = max(r for r in range(12, -1, -1)
                     if r != q and rank_count[r])
        other = _pack(FOUR_KIND, [q, kicker])
    elif trips and (len(trips) > 1 or pairs):
        t = trips[0]
        second = trips[1] if len(trips) > 1 else -1
        if pairs and pairs[0] > second:
            second = pairs[0]
        other = _pack(FULL_HOUSE, [t, second])
    else:
        st = _STRAIGHT[mask]
        if st >= 0:
            other = _pack(STRAIGHT, [st])
        elif trips:
            t = trips[0]
            ks = [r for r in range(12, -1, -1)
                  if r != t and rank_count[r]][:2]
            other = _pack(THREE_KIND, [t] + ks)
        elif len(pairs) >= 2:
            a, b = pairs[0], pairs[1]
            ks = [r for r in range(12, -1, -1)
                  if r != a and r != b and rank_count[r]]
            other = _pack(TWO_PAIR, [a, b, ks[0]])
        elif pairs:
            p = pairs[0]
            ks = [r for r in range(12, -1, -1)
                  if r != p and rank_count[r]][:3]
            other = _pack(ONE_PAIR, [p] + ks)
        else:
            other = _pack(HIGH_CARD, _top_ranks(mask, 5))

    return other if other > best else best


def category_of(score):
    return score // _CATEGORY_SHIFT


def ranks_of(score, count):
    v = score % _CATEGORY_SHIFT
    out = []
    for i in range(count):
        out.append((v >> (4 * (count - 1 - i))) & 0xF)
    return out


def describe(score):
    """役の名前を日本語で返す(『Aハイフラッシュ』のように補足つき)。"""
    cat = category_of(score)
    if cat == STRAIGHT_FLUSH:
        high = ranks_of(score, 1)[0]
        if high == 12:
            return "ロイヤルフラッシュ"
        return "ストレートフラッシュ(%sハイ)" % _RANK_CHAR[high]
    if cat == FOUR_KIND:
        q, _k = ranks_of(score, 2)
        return "フォーカード(%s)" % _RANK_CHAR[q]
    if cat == FULL_HOUSE:
        t, p = ranks_of(score, 2)
        return "フルハウス(%s over %s)" % (_RANK_CHAR[t], _RANK_CHAR[p])
    if cat == FLUSH:
        high = ranks_of(score, 5)[0]
        return "フラッシュ(%sハイ)" % _RANK_CHAR[high]
    if cat == STRAIGHT:
        high = ranks_of(score, 1)[0]
        return "ストレート(%sハイ)" % _RANK_CHAR[high]
    if cat == THREE_KIND:
        t = ranks_of(score, 3)[0]
        return "スリーカード(%s)" % _RANK_CHAR[t]
    if cat == TWO_PAIR:
        a, b, _k = ranks_of(score, 3)
        return "ツーペア(%s と %s)" % (_RANK_CHAR[a], _RANK_CHAR[b])
    if cat == ONE_PAIR:
        p = ranks_of(score, 4)[0]
        return "ワンペア(%s)" % _RANK_CHAR[p]
    high = ranks_of(score, 5)[0]
    return "ハイカード(%s)" % _RANK_CHAR[high]


def straight_high(rank_mask):
    """13bit のランクマスクからストレートの最高位ランクを返す(無ければ -1)。

    ドロー判定(draws.py)でも同じ表を使いたいので公開している。
    """
    return _STRAIGHT[rank_mask]
