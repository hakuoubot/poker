"""
カードの表現
============
52枚のカードを 0〜51 の整数で表す。

    card = rank_index * 4 + suit_index

    rank_index : 0='2', 1='3', ... 8='T', 9='J', 10='Q', 11='K', 12='A'
    suit_index : 0='c'(クラブ), 1='d'(ダイヤ), 2='h'(ハート), 3='s'(スペード)

整数にしておくと評価器の中でビット演算が使えて速い。
麻雀版の shanten.py が牌を文字列で持っているのと違い、
ポーカーは総当たりの試行回数が桁違いに多いので最初から整数にしてある。
"""

RANKS = "23456789TJQKA"
SUITS = "cdhs"

SUIT_SYMBOL = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
SUIT_COLOR = {"c": "green", "d": "blue", "h": "red", "s": "black"}
SUIT_NAME_JA = {"c": "クラブ", "d": "ダイヤ",
                "h": "ハート", "s": "スペード"}

NUM_CARDS = 52
FULL_DECK = tuple(range(NUM_CARDS))

# 入力のゆらぎを吸収する(10 と T、大文字小文字、記号)
_RANK_ALIAS = {"10": "T", "1": "T"}
_SUIT_ALIAS = {
    "♣": "c", "♦": "d", "♥": "h", "♠": "s",
    "C": "c", "D": "d", "H": "h", "S": "s",
}


def make_card(rank_index, suit_index):
    return rank_index * 4 + suit_index


def rank_of(card):
    return card >> 2


def suit_of(card):
    return card & 3


def card_code(card):
    """内部表現 -> 'As' のような英字2文字。"""
    return RANKS[card >> 2] + SUITS[card & 3]


def card_label(card):
    """内部表現 -> 'A♠' のような表示用文字列。"""
    return RANKS[card >> 2] + SUIT_SYMBOL[SUITS[card & 3]]


def card_color(card):
    return SUIT_COLOR[SUITS[card & 3]]


def parse_card(text):
    """'As' / 'as' / '10h' / 'A♠' を内部表現に変換する。"""
    s = text.strip()
    if not s:
        raise ValueError("空のカード指定")
    suit_ch = _SUIT_ALIAS.get(s[-1], s[-1].lower())
    rank_part = s[:-1].upper()
    rank_ch = _RANK_ALIAS.get(rank_part, rank_part)
    if suit_ch not in SUITS or rank_ch not in RANKS:
        raise ValueError("カードとして読めない: %s" % text)
    return make_card(RANKS.index(rank_ch), SUITS.index(suit_ch))


def parse_cards(text):
    """'As Kd 7h' や 'AsKd7h'、'JsTs 9s8d2c' をカードのリストに変換する。

    区切り(空白・カンマ)はあってもなくても良い。まとめて詰めてから2文字ずつ読む。
    """
    s = "".join(text.split()).replace(",", "")
    out = []
    i = 0
    while i < len(s):
        # '10h' のような3文字表記に対応
        step = 3 if s[i:i + 2] == "10" else 2
        out.append(parse_card(s[i:i + step]))
        i += step
    return out


def cards_label(cards):
    return " ".join(card_label(c) for c in cards)


def cards_code(cards):
    return " ".join(card_code(c) for c in cards)
