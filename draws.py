"""
今の役とドロー(アウツ)の分析
============================
「あと何枚のカードで役が上がるか(アウツ)」と、その内訳を出す。

アウツの定義はこのツールでは
**次の1枚で役のカテゴリが上がり、かつ場に出ているカードだけでは作れない役になるカード**
としている。ワンペア → ツーペア以上になるカードは数えるが、
場のカードがペアになっただけ(全員が同じ役になる)のカードは数えない。
キッカーだけが良くなる場合(Aハイ同士でキッカー勝ち)も数えない。

これは実戦でよく言われる「相手に勝てる枚数」とは少しずれる。
たとえばフラッシュドローが完成しても相手がもっと強い場合はあるし、
逆にAハイのままでも勝っている場合はある。正確な勝ち負けは equity.py が
ショーダウンまで回して出しているので、こちらは
「手がどう伸びるか」を人間が把握するための補助と考える。
"""

from cards import card_label, rank_of, suit_of, RANKS
from equity import remaining_deck
from evaluator import (evaluate, category_of, describe, straight_high,
                       CATEGORY_JA)


class DrawInfo:
    def __init__(self):
        self.made = ""          # 今できている役
        self.made_category = 0
        self.outs = []          # アウツになるカード
        self.outs_to = {}       # カテゴリ -> そのカテゴリに上がるカードのリスト
        self.flush_draw = False
        self.straight_draw = ""  # "" / "ガットショット" / "オープンエンド"
        self.backdoor_flush = False
        self.notes = []

    @property
    def out_count(self):
        return len(self.outs)


def analyze(hole, board):
    """今の役とアウツを調べる。board は0〜5枚。"""
    info = DrawInfo()
    cards = list(hole) + list(board)
    score = evaluate(cards) if len(cards) >= 5 else None
    if score is not None:
        info.made = describe(score)
        info.made_category = category_of(score)
    else:
        info.made = "(まだ役は決まらない)"

    if len(board) >= 5:
        info.notes.append("リバーまで開いているのでこれ以上は伸びない")
        return info

    if len(board) == 0:
        info.notes.append("プリフロップはドローを数えない")
        return info

    base_cat = info.made_category
    board = list(board)
    for c in remaining_deck(cards):
        cat = category_of(evaluate(cards + [c]))
        if cat > base_cat and cat > board_category(board + [c]):
            info.outs.append(c)
            info.outs_to.setdefault(cat, []).append(c)

    _detect_flush_draw(info, hole, board)
    _detect_straight_draw(info, hole, board)
    return info


def board_category(board):
    """場のカードだけで作れる役のカテゴリ。

    「そのカードが来ても全員が同じ役になるだけ」を除くために使う。
    5枚に満たない場合は、残りは何の役にもならないカードで埋めたものとみなす。
    """
    rank_count = [0] * 13
    suit_count = [0, 0, 0, 0]
    suit_mask = [0, 0, 0, 0]
    mask = 0
    for c in board:
        r, s = rank_of(c), suit_of(c)
        rank_count[r] += 1
        suit_count[s] += 1
        suit_mask[s] |= 1 << r
        mask |= 1 << r

    best = 0
    for s in range(4):
        if suit_count[s] >= 5:
            best = 8 if straight_high(suit_mask[s]) >= 0 else 5

    quads = any(n == 4 for n in rank_count)
    trips = any(n == 3 for n in rank_count)
    pairs = sum(1 for n in rank_count if n == 2)

    if quads:
        other = 7
    elif trips and pairs:
        other = 6
    elif straight_high(mask) >= 0:
        other = 4
    elif trips:
        other = 3
    elif pairs >= 2:
        other = 2
    elif pairs == 1:
        other = 1
    else:
        other = 0
    return max(best, other)


def _detect_flush_draw(info, hole, board):
    suits = [0, 0, 0, 0]
    hole_suits = [0, 0, 0, 0]
    for c in list(hole) + list(board):
        suits[suit_of(c)] += 1
    for c in hole:
        hole_suits[suit_of(c)] += 1

    for s in range(4):
        if suits[s] == 4 and hole_suits[s] >= 1:
            info.flush_draw = True
            info.notes.append("フラッシュドロー(あと1枚で完成)")
        elif suits[s] == 3 and hole_suits[s] >= 1 and len(board) == 3:
            info.backdoor_flush = True
            info.notes.append("バックドアフラッシュ(ターンとリバー両方が必要)")


def _detect_straight_draw(info, hole, board):
    cards = list(hole) + list(board)
    mask = 0
    for c in cards:
        mask |= 1 << rank_of(c)

    def has_straight(m):
        for high in range(12, 3, -1):
            need = 0
            for i in range(5):
                need |= 1 << (high - i)
            if m & need == need:
                return True
        wheel = (1 << 12) | (1 << 3) | (1 << 2) | (1 << 1) | 1
        return m & wheel == wheel

    if has_straight(mask):
        return

    hits = []
    for r in range(13):
        if mask >> r & 1:
            continue
        if has_straight(mask | (1 << r)):
            hits.append(r)

    if not hits:
        return
    ranks = ", ".join(RANKS[r] for r in sorted(hits, reverse=True))
    if len(hits) >= 2:
        info.straight_draw = "オープンエンド"
        info.notes.append("ストレートドロー(両面 / %s のどれかで完成)" % ranks)
    else:
        info.straight_draw = "ガットショット"
        info.notes.append("ガットショット(%s だけで完成)" % ranks)


def summary_lines(info):
    """GUI・CLI 表示用の行のリスト。"""
    lines = ["今の役: %s" % info.made]
    if info.outs:
        lines.append("アウツ: %d枚" % info.out_count)
        for cat in sorted(info.outs_to, reverse=True):
            cs = info.outs_to[cat]
            shown = " ".join(card_label(c) for c in cs[:12])
            if len(cs) > 12:
                shown += " ほか%d枚" % (len(cs) - 12)
            lines.append("  %s へ %d枚: %s"
                         % (CATEGORY_JA[cat], len(cs), shown))
    for n in info.notes:
        lines.append("・" + n)
    return lines
