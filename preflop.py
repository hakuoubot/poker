"""
プリフロップのレンジ(6人リング・キャッシュ前提)
==============================================
ホールデムのプリフロップは「勝率」より「レンジ表」で決めるのが実戦的なので、
6-max の標準的なオープンレンジと、レイズに対する 3ベット/コールのレンジを
表として持っている。

レンジは "22+, ATs+, KTs+, AJo+" のような一般的な表記で書き、
expand() で 169 種類のハンド表記(AKs / AKo / TT)の集合に、
range_combos() で実際のカード2枚の組み合わせ(全1326通りのうち該当分)に展開する。
展開した組み合わせは equity.py の opp_range にそのまま渡せる。

*** レンジ表の性格について ***
ここに入っている数字は GTO ソルバーの出力そのものではなく、
広く出回っている 6-max の標準的なチャートに合わせた「よくある形」。
ソルバーが出す混合戦略(同じハンドを50%レイズ50%フォールド、など)は
再現していない。判断の出発点として使い、細部は自分の感覚で調整する前提。
"""

from itertools import combinations

from cards import RANKS, NUM_CARDS, rank_of, suit_of

POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")

POSITION_JA = {
    "UTG": "UTG(最初に行動)",
    "HJ": "HJ(ハイジャック)",
    "CO": "CO(カットオフ)",
    "BTN": "BTN(ボタン)",
    "SB": "SB(スモールブラインド)",
    "BB": "BB(ビッグブラインド)",
}

# ポストフロップの行動順。SB→BB→UTG→…→BTN の順に動く。
_POSTFLOP_ORDER = {"SB": 0, "BB": 1, "UTG": 2, "HJ": 3, "CO": 4, "BTN": 5}


def is_in_position(hero, villain):
    """フロップ以降、hero が villain より後に行動する(ポジションがある)なら True。"""
    return _POSTFLOP_ORDER[hero] > _POSTFLOP_ORDER[villain]


# ---------------------------------------------------------
# オープンレイズ(誰も入っていないところから最初に上げる)
# ---------------------------------------------------------
RFI = {
    # 括弧内は全ハンドに対する割合。一般的な 6-max チャートの広さに合わせてある。
    "UTG": "22+, ATs+, A5s-A2s, KTs+, QTs+, JTs, T9s, 98s, "
           "AJo+, KQo",                                        # 14.3%
    "HJ":  "22+, A8s+, A5s-A2s, K9s+, QTs+, J9s+, T9s, 98s, 87s, 76s, "
           "ATo+, KJo+, QJo",                                  # 18.9%
    "CO":  "22+, A2s+, K7s+, Q8s+, J8s+, T7s+, 96s+, 86s+, 75s+, 65s, "
           "54s, A9o+, KTo+, QTo+, JTo",                       # 27.0%
    "BTN": "22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 95s+, 84s+, 74s+, 63s+, "
           "53s+, 43s, A2o+, K8o+, Q9o+, J9o+, T9o, 98o, 87o",  # 45.7%
    "SB":  "22+, A2s+, K4s+, Q6s+, J7s+, T7s+, 96s+, 86s+, 75s+, 65s, "
           "54s, A2o+, K9o+, Q9o+, J9o+, T9o",                 # 38.8%
    "BB":  "",   # BB は最初に上げる立場にならない
}

# ---------------------------------------------------------
# レイズに対する対応
# 相手の位置を EP(UTG,HJ) / LP(CO,BTN) / SB の3つにまとめ、
# 自分がポジションを取れるかどうかで分けている。
# ソルバーは相手の席ごとに細かく変えるが、覚えて使える粒度に寄せた近似。
# ---------------------------------------------------------
VS_RAISE = {
    # (相手のグループ, 自分にポジションがあるか): (3ベット, コール)
    ("EP", True):  ("TT+, AQs+, AKo, A5s-A4s",
                    "77-99, AJs-ATs, KQs, KJs, QJs, JTs, T9s, 98s, AQo"),
    ("EP", False): ("JJ+, AQs+, AKo",
                    "22-TT, AJs-ATs, KQs, KJs, QJs, JTs, T9s, AQo"),
    ("LP", True):  ("99+, AJs+, KJs, KQs, AQo+, A5s-A2s",
                    "22-88, ATs-A6s, KTs, QJs, QTs, JTs, J9s, T9s, 98s, "
                    "87s, 76s, AJo, KQo"),
    ("LP", False): ("TT+, AQs+, AKo, A5s-A3s, KJs+",
                    "22-99, AJs-A6s, KTs+, QTs+, J9s+, T9s, 98s, 87s, "
                    "76s, AJo, ATo, KQo, KJo, QJo"),
    ("SB", False): ("99+, AJs+, KQs, AQo+, A5s-A2s",
                    "22-88, ATs-A6s, KTs, QTs, JTs, J9s, T9s, 98s, 87s, "
                    "76s, 65s, AJo, ATo, KQo, KJo, QJo, JTo"),
    ("SB", True):  ("99+, AJs+, KQs, AQo+, A5s-A2s",
                    "22-88, ATs-A6s, KTs, QTs, JTs, J9s, T9s, 98s, 87s, "
                    "76s, 65s, AJo, ATo, KQo, KJo, QJo, JTo"),
}


# 3ベットされた側の対応。3ベットは相手のレンジが一気に強くなるので、
# オープンに対する対応(VS_RAISE)とは別の表が要る。
# 4ベットは価値のある手だけ + A5s/A4s をブラフに混ぜる形が標準。
VS_THREEBET = ("QQ+, AKs, AKo, A5s-A4s",
               "99-JJ, AQs, AJs, KQs, AQo")

# 相手のプリフロップの行動。レンジがまったく違うので分ける。
PREFLOP_ACTIONS = ("オープンしてきた", "3ベットしてきた", "コールしただけ")


def raiser_group(pos):
    if pos in ("UTG", "HJ"):
        return "EP"
    if pos in ("CO", "BTN"):
        return "LP"
    return "SB"


# ---------------------------------------------------------
# ハンド表記(169種類)
# ---------------------------------------------------------
def hand_class(hole):
    """カード2枚 -> 'AKs' / 'AKo' / 'TT' の表記。"""
    a, b = hole
    ra, rb = rank_of(a), rank_of(b)
    if ra < rb:
        ra, rb = rb, ra
        a, b = b, a
    hi, lo = RANKS[ra], RANKS[rb]
    if ra == rb:
        return hi + lo
    return hi + lo + ("s" if suit_of(a) == suit_of(b) else "o")


def _ri(ch):
    return RANKS.index(ch.upper())


def _parse_token(token):
    """'22+' 'ATs+' 'A5s-A2s' 'KQo' などを表記の集合に変換する。"""
    t = token.strip().replace(" ", "")
    if not t:
        return set()

    if "-" in t:                                   # 範囲指定
        left, right = t.split("-", 1)
        return _expand_span(left, right)

    plus = t.endswith("+")
    if plus:
        t = t[:-1]

    if len(t) == 2 and t[0].upper() == t[1].upper():        # ペア
        r = _ri(t[0])
        rs = range(r, 13) if plus else [r]
        return {RANKS[x] + RANKS[x] for x in rs}

    if len(t) == 3 and t[2] in "so":               # スーテッド / オフスーツ
        hi, lo, suf = _ri(t[0]), _ri(t[1]), t[2]
        if hi < lo:
            hi, lo = lo, hi
        los = range(lo, hi) if plus else [lo]
        return {RANKS[hi] + RANKS[x] + suf for x in los}

    if len(t) == 2:                                # 'AK' は両方
        hi, lo = _ri(t[0]), _ri(t[1])
        if hi < lo:
            hi, lo = lo, hi
        los = range(lo, hi) if plus else [lo]
        out = set()
        for x in los:
            out.add(RANKS[hi] + RANKS[x] + "s")
            out.add(RANKS[hi] + RANKS[x] + "o")
        return out

    raise ValueError("レンジ表記として読めない: %s" % token)


def _expand_span(left, right):
    """'TT-77' や 'A5s-A2s' のような範囲。"""
    if len(left) == 2 and left[0].upper() == left[1].upper():
        a, b = _ri(left[0]), _ri(right[0])
        lo, hi = min(a, b), max(a, b)
        return {RANKS[x] * 2 for x in range(lo, hi + 1)}

    if len(left) != 3 or len(right) != 3 or right[2] != left[2] \
            or _ri(right[0]) != _ri(left[0]):
        raise ValueError("範囲指定は同じ高位カード・同じ種類のみ: %s-%s"
                         % (left, right))
    suf = left[2]
    hi = _ri(left[0])
    a, b = _ri(left[1]), _ri(right[1])
    lo_r, hi_r = min(a, b), max(a, b)
    return {RANKS[hi] + RANKS[x] + suf for x in range(lo_r, hi_r + 1)}


def expand(text):
    """レンジ表記の文字列 -> ハンド表記の集合。"""
    if not isinstance(text, str):
        return set(text)
    out = set()
    for token in text.split(","):
        if token.strip():
            out |= _parse_token(token)
    return out


def _build_class_combos():
    """169表記 -> カード2枚の組み合わせ。起動時に一度だけ作る。"""
    table = {}
    for a, b in combinations(range(NUM_CARDS), 2):
        table.setdefault(hand_class((a, b)), []).append((a, b))
    return table


CLASS_COMBOS = _build_class_combos()
TOTAL_COMBOS = sum(len(v) for v in CLASS_COMBOS.values())   # 1326


def range_combos(spec, dead=()):
    """レンジ -> カード2枚の組み合わせのリスト。dead のカードを含む分は除く。"""
    classes = expand(spec)
    dead = set(dead)
    out = []
    for cls in classes:
        for combo in CLASS_COMBOS.get(cls, ()):
            if combo[0] not in dead and combo[1] not in dead:
                out.append(combo)
    return out


def range_percent(spec):
    """レンジが全ハンドの何%かを返す。レンジ表の広さの確認用。"""
    classes = expand(spec)
    n = sum(len(CLASS_COMBOS.get(c, ())) for c in classes)
    return 100.0 * n / TOTAL_COMBOS


# ---------------------------------------------------------
# 判断
# ---------------------------------------------------------
def rfi_action(pos, hole):
    """誰も上げていない場面での推奨。('レイズ'/'フォールド', 補足) を返す。"""
    cls = hand_class(hole)
    rng = RFI.get(pos, "")
    if not rng:
        return "フォールド", "BB はオープンレイズの立場にならない"
    if cls in expand(rng):
        return "レイズ", "%s のオープンレンジ(上位%.0f%%)に入っている" % (
            pos, range_percent(rng))
    return "フォールド", "%s のオープンレンジ(上位%.0f%%)の外" % (
        pos, range_percent(rng))


def vs_raise_action(hero_pos, raiser_pos, hole):
    """誰かが上げてきた場面での推奨。('3ベット'/'コール'/'フォールド', 補足)。"""
    cls = hand_class(hole)
    grp = raiser_group(raiser_pos)
    ip = is_in_position(hero_pos, raiser_pos)
    three, call = VS_RAISE.get((grp, ip), VS_RAISE[(grp, False)])
    if cls in expand(three):
        return "3ベット", "%s のレイズに対する3ベットレンジ(%.0f%%)" % (
            raiser_pos, range_percent(three))
    if cls in expand(call):
        note = "%s のレイズに対するコールレンジ(%.0f%%)" % (
            raiser_pos, range_percent(call))
        if not ip:
            note += " / ポジションが無いぶん慎重に"
        return "コール", note
    return "フォールド", "%s のレイズに対しては降りるハンド" % raiser_pos


def opponent_range(raiser_pos, aggressive=True, hero_pos=None,
                   preflop_action="オープンしてきた"):
    """相手のレンジ推定。

    preflop_action で大きく変わる。ここを見ないと、**3ベットされた場面でも
    相手をオープンレンジ(20〜45%)のまま扱ってしまい、自分の勝率が
    実際よりずっと高く出る**。3ベットレンジは上位4〜6%しかない。
    """
    if preflop_action == "3ベットしてきた" and hero_pos:
        ip = is_in_position(raiser_pos, hero_pos)
        three, _call = VS_RAISE.get((raiser_group(hero_pos), ip),
                                    VS_RAISE[(raiser_group(hero_pos), False)])
        return three
    if preflop_action == "コールしただけ" and hero_pos:
        ip = is_in_position(raiser_pos, hero_pos)
        _three, call = VS_RAISE.get((raiser_group(hero_pos), ip),
                                    VS_RAISE[(raiser_group(hero_pos), False)])
        return call

    if raiser_pos == "BB":
        # BB はオープンレイズをしない立場なので、代わりに
        # 「レイズに対して降りずに続ける範囲」を持っているとみなす
        three, call = VS_RAISE[("LP", False)]
        return three + ", " + call
    rng = RFI.get(raiser_pos) or RFI["CO"]
    if aggressive:
        return rng
    return rng + ", " + RFI["BTN"]


def vs_threebet_action(hero_pos, raiser_pos, hole):
    """3ベットされた場面での推奨。('4ベット'/'コール'/'フォールド', 補足)。

    3ベットは相手のレンジが一気に狭くなる(上位5%前後)ので、
    オープンに対する対応とはまったく別の判断になる。
    """
    cls = hand_class(hole)
    four, call = VS_THREEBET
    villain = opponent_range(raiser_pos, hero_pos=hero_pos,
                             preflop_action="3ベットしてきた")
    if cls in expand(four):
        return "4ベット", "3ベットに対する4ベットレンジ(%.0f%%)" % range_percent(four)
    if cls in expand(call):
        return "コール", "3ベットに対するコールレンジ(%.0f%%)" % range_percent(call)
    return ("フォールド",
            "3ベットされたら降りるハンド(相手は上位%.0f%%しかない)"
            % range_percent(villain))
