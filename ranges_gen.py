"""
スタックの深さに合わせたプリフロップのレンジ表を作る
==================================================
今あるレンジ表(`preflop.RFI`)は 100BB のキャッシュ用。ターボトーナメントでは
スタックが浅くなるうえ順位ポイントで判断するので、そのまま使うとズレる。
特に **25〜40BB 帯**は、オールイン一択でもキャッシュ用チャートでもない、
判断が一番難しい帯域なのに何も持っていなかった。

そこで表を探してくるのではなく、**今のスタック構成で計算して作る**。
169ハンドそれぞれについて

    降りる / 2.2BB にレイズ / オールイン

を順位ポイントの期待値で比べ、一番良いものをそのハンドの答えにする。
`icm.py` と `pushfold.py` の上に載っているだけで、新しい理論は使っていない。

使い方
------
GUI の「このスタックのレンジ表を作る」ボタン、または

    python ranges_gen.py

169ハンド分の勝率計算が要るので30秒前後かかる。

分かっている近似
----------------
- **コールされた後の取り分は当てはめの定数**(0.82)。実測ではない。
  生成されるレンジの広さが一般的なチャートに収まるように選んである。
  ソルバーで実測しようとしたが、この用途には使えなかった(理由は下の
  REALIZATION のコメントと HANDOFF 追記6)。ここが今も最大の未検証点。
- 続けてくる確率は席ごとの固定値(BB 42% / SB 16% / それ以外 11%)。
  BB は既に1BB置いていてオッズが良いので広く受ける。
- 3ベットで返されたら降りる前提。自分から4ベットする分は入れていない。
"""

import sys

import icm
import preflop as pf
import pushfold
from cards import RANKS, make_card
from equity import equity

# 2.2BB のオープンに対して降りずに続けてくる割合。席によって大きく違う。
# BB は既に1BB置いていて オッズが良いので広く受ける。SB は逆に降りやすい。
CONTINUE_BB = 0.42
CONTINUE_SB = 0.16
CONTINUE_OTHER = 0.11
# 続けてくる相手のレンジ(勝率計算に使う)。上の割合とだいたい同じ広さ。
CONTINUE_RANGE = ("22+, A2s+, K5s+, Q7s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, "
                  "A7o+, A5o-A2o, KTo+, QTo+, JTo, T9o")
# 3ベットで返されて降ろされる割合(自分の4ベットは考えない、控えめな見積り)
THREEBET = 0.07

OPEN_SIZE = 2.2          # オープンレイズの大きさ(BB)。ターボの標準的な小さめ

# コールされた後にポストフロップで追加で入る額の目安(その時点のポットに対する割合)
POSTFLOP_EXTRA = 0.5
# コールされた後の取り分の見積り。素の勝率にこの割合を掛けたものを
# 「勝つ確率」として扱う。
#
# *** この値の性格(重要) ***
# これは実測値ではない。**生成されるレンジの広さが、一般的な 6-max の
# チャートの広さに収まるように選んだ当てはめの定数**。
#
# calibrate.py でソルバーから実測を試みたが、この用途には使えなかった。
# 測れるのは「解かれたレンジの中での、その手の取り分」であって、
# 「そのレンジにその手を足したときの取り分」ではない。弱い手が正の取り分を
# 持つのは、同じレンジに強い手が入っていてブラフが通るからで、
# その値を根拠にレンジを広げるのは循環している。
# 実際に測定値を入れたら 169ハンド全部がレイズになった(HANDOFF 追記6)。
#
# 測定そのものは有効で、別のことは分かった:
#   ポジションのある側はレンジ全体では素の勝率以上に取る(1.26 / OOPは0.79)。
REALIZATION_IP = 0.82
REALIZATION_OOP = 0.82 * (0.79 / 1.26)
REALIZATION = REALIZATION_IP


FOLD = "降りる"
RAISE = "レイズ"
ALLIN = "オールイン"

# 表示・色分け用
ACTION_COLORS = {ALLIN: "#c62828", RAISE: "#ef9a3f", FOLD: "#dcdcd8"}


def _representative(cls):
    """'AKs' -> 代表となるカード2枚。同じ表記ならどれでも勝率は同じ。"""
    hi, lo = RANKS.index(cls[0]), RANKS.index(cls[1])
    if len(cls) == 2:
        return [make_card(hi, 3), make_card(lo, 2)]
    if cls[2] == "s":
        return [make_card(hi, 3), make_card(lo, 3)]
    return [make_card(hi, 3), make_card(lo, 2)]


def all_classes():
    """169表記を、表に並べる順(強い順ではなくランク順)で返す。"""
    out = []
    for i in range(12, -1, -1):
        row = []
        for j in range(12, -1, -1):
            if i == j:
                row.append(RANKS[i] * 2)
            elif i > j:
                row.append(RANKS[i] + RANKS[j] + "s")
            else:
                row.append(RANKS[j] + RANKS[i] + "o")
        out.append(row)
    return out


def _raise_points(sit, hole, iters):
    """2.2BB にレイズしたときの順位ポイント期待値(降りるを0とした差)。

    3つに分かれる。

    1. 後ろが全員降りる      -> ポットをそのまま取る
    2. 3ベットで返される     -> 降りてレイズ分を失う
    3. コールされる          -> ポストフロップに進む

    3 は厳密には解けない。以前は「勝率はそのままは実現できない」という
    当てずっぽうの係数で近似していたが、いまは calibrate.py で
    **ソルバーから実測した実現率**を使っている(下のコメント参照)。
    """
    stacks = sit.table_stacks()
    pts = sit.points
    cost = sit.orbit_cost()
    R = min(OPEN_SIZE, sit.stack)

    behind = pushfold.PLAYERS_BEHIND.get(sit.hero_pos, 2)
    blinds = min(pushfold.BLINDS_BEHIND.get(sit.hero_pos, 2), behind)
    others = max(behind - blinds, 0)
    stay = (1 - CONTINUE_OTHER) ** others
    if blinds >= 2:
        stay *= (1 - CONTINUE_SB) * (1 - CONTINUE_BB)
    elif blinds == 1:
        stay *= (1 - CONTINUE_BB)
    all_fold = stay
    three_bet = (1 - all_fold) * THREEBET
    called = 1 - all_fold - three_bet

    combos = pf.range_combos(CONTINUE_RANGE, dead=hole)
    raw = equity(hole, [], opponents=1, opp_range=combos,
                 iters=iters, seed=sit.seed).equity if combos else 0.5
    # SB からのオープンだけは BB が後ろに残るのでポジションが無い
    realization = REALIZATION_OOP if sit.hero_pos == "SB" else REALIZATION_IP
    eq = min(raw * realization, 1.0)

    pot_after = sit.pot + 2 * R
    extra = min(POSTFLOP_EXTRA * pot_after, sit.stack - R)
    extra = max(extra, 0.0)
    invest = R + extra

    def pt(state):
        return icm.hero_points(icm.after_blinds(state, cost), pts)

    win, lose, villain_folds, hero_folds = icm.confrontation_stacks(
        stacks, 0, 1, sit.pot, invest, 0.0)
    _w2, lose_3bet, _v2, _h2 = icm.confrontation_stacks(
        stacks, 0, 1, sit.pot, R, 0.0)

    value = (all_fold * pt(villain_folds)
             + three_bet * pt(lose_3bet)
             + called * (eq * pt(win) + (1 - eq) * pt(lose)))
    return value - pt(hero_folds)


# 際どいハンドを引き直す閾値。順位EVがこれより0に近ければ精度を上げ直す
BORDERLINE = 0.15
REFINE_ITERS = 24000


def _decide(sit, cls, iters):
    """1ハンドぶんの判断。(行動, 順位EV) を返す。"""
    import copy
    hole = _representative(cls)
    local = copy.copy(sit)
    local.hole = hole

    push = pushfold.advise(local, iters=iters).push_points
    raise_pt = _raise_points(local, hole, iters)

    best, value = FOLD, 0.0
    if raise_pt > value:
        best, value = RAISE, raise_pt
    if push > value:
        best, value = ALLIN, push
    return best, value


def generate(sit, iters=4000, progress=None, should_stop=None):
    """169ハンドの最善手。{'AKs': (行動, 順位EV)} を返す。

    まず粗く全部を見て、**答えが際どいハンドだけ試行回数を上げて引き直す**。
    全部を高精度でやると数分かかるが、判断が割れるのは境目のハンドだけなので
    そこにだけ手をかければ済む。粗いままだと、
    「中間の手が降りで、それより弱い手がレイズ」のようなムラが出る。
    """
    table = {}
    rows = all_classes()
    flat = [c for row in rows for c in row]
    total = len(flat)

    for i, cls in enumerate(flat):
        if should_stop and should_stop():
            raise KeyboardInterrupt("中止した")
        table[cls] = _decide(sit, cls, iters)
        if progress and i % 5 == 0:
            progress(0.7 * (i + 1) / total)

    borderline = [c for c, (_a, v) in table.items() if abs(v) < BORDERLINE]
    for i, cls in enumerate(borderline):
        if should_stop and should_stop():
            raise KeyboardInterrupt("中止した")
        table[cls] = _decide(sit, cls, REFINE_ITERS)
        if progress:
            progress(0.7 + 0.3 * (i + 1) / max(len(borderline), 1))
    if progress:
        progress(1.0)
    return table


def compare_with_cash(table, pos):
    """生成した表と、キャッシュ用のオープンレンジのズレを言葉にする。"""
    cash = pf.expand(pf.RFI.get(pos) or "")
    play = {c for c, (a, _v) in table.items() if a != FOLD}
    only_new = sorted(play - cash)
    only_cash = sorted(cash - play)
    return {
        "生成した表で参加": len(play),
        "生成した表の割合": pf.range_percent(play),
        "キャッシュ表で参加": len(cash),
        "キャッシュ表の割合": pf.range_percent(cash) if cash else 0.0,
        "生成側だけ参加": only_new,
        "キャッシュ側だけ参加": only_cash,
    }


def as_text(table):
    """13x13 の表を文字で。オ=オールイン レ=レイズ ・=降りる"""
    mark = {ALLIN: "オ", RAISE: "レ", FOLD: "・"}
    lines = []
    for row in all_classes():
        lines.append(" ".join(mark[table[c][0]] for c in row))
    return "\n".join(lines)


def _main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import time
    import strategy as st
    from cards import parse_cards

    pos = sys.argv[1] if len(sys.argv) > 1 else "BTN"
    stack = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    ante = 0.1
    sit = st.Situation(parse_cards("AsKs"), [], hero_pos=pos,
                       villain_pos="BB", pot=1.5 + ante * 6, to_call=0,
                       stack=stack, seed=1, tournament=True,
                       points=icm.POINT_PRESETS[icm.DEFAULT_PRESET],
                       villain_stack=25, other_stacks=[30, 28, 35, 20],
                       ante=ante)

    print("%s / %.0fBB のレンジ表を作ります(30秒ほど)" % (pos, stack))
    t0 = time.perf_counter()
    table = generate(sit)
    print(as_text(table))
    print("%.0f秒" % (time.perf_counter() - t0))

    d = compare_with_cash(table, pos)
    print("\n参加する割合: 生成 %.0f%% / キャッシュ表 %.0f%%  (表記の数では %d / %d)"
          % (d["生成した表の割合"], d["キャッシュ表の割合"],
             d["生成した表で参加"], d["キャッシュ表で参加"]))
    print("生成側だけ参加: %s" % (", ".join(d["生成側だけ参加"]) or "なし"))
    print("キャッシュ側だけ参加: %s"
          % (", ".join(d["キャッシュ側だけ参加"]) or "なし"))


if __name__ == "__main__":
    _main()
