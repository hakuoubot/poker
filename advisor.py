"""
対話式CLI(GUIを使わずに手早く見たいとき)
========================================
    python advisor.py

カードは 'AsKd' や 'As Kd' のように英字で入力する。
    ランク: 2 3 4 5 6 7 8 9 T J Q K A   (10 は T でも 10 でも可)
    スート: s(スペード) h(ハート) d(ダイヤ) c(クラブ)

コンソールが Shift-JIS だと ♠ などが出せないので、CLI では英字表記を使う。
"""

import sys

import draws
import icm
import strategy as st
from cards import parse_cards, card_code, cards_code
from equity import standard_error


def _ask(prompt, default=""):
    s = input("%s%s: " % (prompt, " [%s]" % default if default else ""))
    s = s.strip()
    return s or default


def _ask_float(prompt, default):
    while True:
        s = _ask(prompt, str(default))
        try:
            return float(s)
        except ValueError:
            print("  数字で入力してください")


def _ask_choice(prompt, choices, default):
    while True:
        s = _ask("%s (%s)" % (prompt, "/".join(choices)), default)
        if s in choices:
            return s
        if s.upper() in choices:
            return s.upper()
        print("  %s のどれかを入力してください" % "/".join(choices))


def one_round():
    hole = parse_cards(_ask("自分の2枚 (例 AsKd)"))
    if len(hole) != 2:
        print("2枚で入力してください")
        return
    board_text = _ask("場のカード 0/3/4/5枚 (例 9s8d2c、無ければ空Enter)")
    board = parse_cards(board_text) if board_text else []
    if len(board) not in (0, 3, 4, 5):
        print("場のカードは0,3,4,5枚のいずれかです")
        return

    hero = _ask_choice("自分の位置", ["UTG", "HJ", "CO", "BTN", "SB", "BB"],
                       "BTN")
    villain = _ask_choice("相手の位置",
                          ["UTG", "HJ", "CO", "BTN", "SB", "BB"], "CO")
    opponents = int(_ask_float("最後まで残る相手の人数", 1))
    pot = _ask_float("ポット(相手のベット込み、BB単位)", 6)
    to_call = _ask_float("自分が払う額(0なら誰もベットしていない)", 4)
    stack = _ask_float("自分の残りスタック", 97)

    action = "なし"
    if board:
        action = _ask_choice("相手の前のストリートの行動",
                             list(st.VILLAIN_ACTION_KEEP), "なし")

    tour = _ask("トーナメント(順位ポイント)で判断する? y/n", "y").lower() != "n"
    points = villain_stack = None
    others = []
    if tour:
        points = icm.POINT_PRESETS[icm.DEFAULT_PRESET]
        villain_stack = _ask_float("相手のスタック(BB)", 25)
        text = _ask("他の人のスタック(カンマ区切り)", "30,28,35,20")
        others = [float(x) for x in text.split(",") if x.strip()]

    sit = st.Situation(hole, board, hero_pos=hero, villain_pos=villain,
                       opponents=opponents, pot=pot, to_call=to_call,
                       stack=stack, iters=20000,
                       tournament=tour, points=points,
                       villain_stack=villain_stack, other_stacks=others,
                       villain_action=action)

    print("\n計算中...")
    a = st.analyze(sit)

    eq = a.equity
    print("\n=== %s  %s / %s ==="
          % (a.street, cards_code(hole), cards_code(board) or "-"))
    print("勝率 %.1f%%  (勝ち %.1f%% / 引き分け %.1f%% / 負け %.1f%%, 誤差 ±%.1f%%)"
          % (eq.equity * 100, eq.win * 100, eq.tie * 100, eq.lose * 100,
             standard_error(eq) * 200))
    if a.pot_odds is not None:
        print("必要勝率(ポットオッズ) %.1f%%" % (a.pot_odds * 100))
    if a.icm_pot_odds is not None:
        print("必要勝率(ICM込み) %.1f%%" % (a.icm_pot_odds * 100))
    if a.finish:
        print("今の順位確率: %s" % a.finish)
    if a.chart:
        hand, note = a.chart
        print("レンジ表の推奨: %s  (%s)" % (hand, note))

    print("\n[手の状況]")
    for line in draws.summary_lines(a.draw):
        print("  " + line.replace("♠", "s").replace("♥", "h")
                          .replace("♦", "d").replace("♣", "c"))

    if a.pushfold:
        print("\n[オールインするか降りるか]")
        for line in a.pushfold.as_lines():
            print("  " + line)

    print("\n[アクションの優先度]")
    for i, o in enumerate(a.options):
        mark = "★" if i == 0 else "  "
        extra = ("  順位EV %+.2f pt" % o.icm_ev) if o.icm_ev is not None else ""
        print("%s %-24s EV %+7.2f BB%s" % (mark, o.name, o.ev, extra))
        for d in o.detail:
            print("      - %s" % d)

    print("\n[注意]")
    for n in a.notes:
        print("  ・" + n)
    print()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("テキサスホールデム アクションアドバイザー(6人卓)")
    print("既定はトーナメント(ポーカーチェイスのランク戦)として順位ポイントで判断します")
    print("Ctrl+C で終了\n")
    while True:
        try:
            one_round()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します")
            return
        except ValueError as exc:
            print("入力エラー: %s\n" % exc)


if __name__ == "__main__":
    main()
