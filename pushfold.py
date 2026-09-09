"""
プッシュ/フォールド(オールインするか降りるか)
============================================
ターボトーナメントはブラインドがすぐ上がるので、スタックが浅くなると
「オールインするか降りるか」の二択がほとんどになる。ポーカーチェイスの
ランク戦もこの形。ここはチャートを丸暗記するより、
**今のスタック構成で順位ポイントの期待値を計算した方が正確**なので計算している。

やっていること
--------------
1. 自分の後ろに残っている人数を数える(ポジションから決まる)
2. 各自が「この額のオールインにコールしてくる範囲」を、スタックの深さから決める
3. 全員降りる確率と、誰か1人がコールしてくる確率に分ける
4. それぞれの結果を ICM(順位ポイント)に換算して足す

分かっている近似
----------------
- **コールするのは多くても1人**とみなしている。2人以上に囲まれる分は無視。
  そのぶんプッシュの評価は少し甘くなる。
- ブラインドの2人は既にチップを置いているぶん広くコールしてくるので、
  それ以外の人のコール範囲はブラインドの 45% の広さとして扱っている。
  ここを同じ広さにすると、後ろに人が多いポジションで
  オールインが不当に不利に出る(BTN の10BBが降り推奨になってしまう)。
- コールレンジは公開されているプッシュ/フォールド表に近い広さの固定値。
  相手の癖には合わせていない。
- 相手同士のスタック差は見ているが、誰がコールしやすいかの偏りは見ていない。
"""

import preflop as pf
import icm
from equity import equity

# 後ろに残っている人数(6人卓)
PLAYERS_BEHIND = {"UTG": 5, "HJ": 4, "CO": 3, "BTN": 2, "SB": 1, "BB": 0}
# そのうちブラインド(SB/BB)が何人か
BLINDS_BEHIND = {"UTG": 2, "HJ": 2, "CO": 2, "BTN": 2, "SB": 1, "BB": 0}
# ブラインド以外の人のコール範囲は、ブラインドの何倍の広さか
NON_BLIND_RATIO = 0.45

# オールインの大きさ(BB)ごとの、相手のコールレンジ。
# 浅いほど広くコールされる。よく出回っているプッシュ/フォールド表の
# コール側におおむね合わせてある。
CALL_RANGES = [
    (6, "22+, A2s+, K2s+, Q5s+, J7s+, T7s+, 97s+, 87s, 76s, "
        "A2o+, K7o+, Q9o+, J9o+, T9o"),
    (8, "22+, A2s+, K5s+, Q8s+, J8s+, T8s+, 98s, 87s, "
        "A2o+, K9o+, QTo+, JTo"),
    (10, "22+, A2s+, K8s+, Q9s+, J9s+, T9s, A2o+, KTo+, QJo"),
    (12, "22+, A2s+, K9s+, QTs+, JTs, A5o+, KJo+"),
    (15, "33+, A2s+, KTs+, QJs, A8o+, KQo"),
    (20, "55+, A5s+, KJs+, ATo+, KQo"),
    (99, "77+, A9s+, KQs, AJo+"),
]


def calling_range(push_bb):
    """この大きさのオールインに、相手がコールしてくる範囲。"""
    for limit, spec in CALL_RANGES:
        if push_bb <= limit:
            return spec
    return CALL_RANGES[-1][1]


class PushFoldResult:
    def __init__(self):
        self.push_points = 0.0      # オールインの順位EV(降りるを0とした差)
        self.fold_points = 0.0
        self.all_fold_prob = 0.0
        self.call_prob = 0.0
        self.equity_vs_call = 0.0
        self.behind = 0
        self.call_spec = ""
        self.recommend = ""
        self.notes = []

    def as_lines(self):
        out = [
            "推奨: %s" % self.recommend,
            "オールインの順位EV %+.2f pt(降りる = 0)" % self.push_points,
            "全員降りる確率 %.0f%% / 誰かがコール %.0f%%"
            % (self.all_fold_prob * 100, self.call_prob * 100),
        ]
        if self.call_prob > 0:
            out.append("コールされたときの勝率 %.1f%%"
                       % (self.equity_vs_call * 100))
        out.append("後ろに %d人 / ブラインドの想定コールレンジ %.0f%%"
                   % (self.behind, pf.range_percent(self.call_spec)))
        return out + self.notes


def advise(sit, iters=8000):
    """今の局面でオールインすべきか。プリフロップ・自分から仕掛ける場面用。"""
    r = PushFoldResult()
    stacks = sit.table_stacks()
    pts = sit.points
    push = min(sit.stack, max(s for s in stacks[1:]) + sit.pot)

    r.behind = PLAYERS_BEHIND.get(sit.hero_pos, 2)
    blinds = min(BLINDS_BEHIND.get(sit.hero_pos, 2), r.behind)
    others = max(r.behind - blinds, 0)
    r.call_spec = calling_range(sit.stack)
    w_blind = pf.range_percent(r.call_spec) / 100.0
    w_other = w_blind * NON_BLIND_RATIO

    r.all_fold_prob = (1 - w_blind) ** blinds * (1 - w_other) ** others
    r.call_prob = 1 - r.all_fold_prob

    combos = pf.range_combos(r.call_spec, dead=sit.hole)
    if combos:
        eq = equity(sit.hole, [], opponents=1, opp_range=combos,
                    iters=iters, seed=sit.seed)
        r.equity_vs_call = eq.equity
    else:
        r.equity_vs_call = 0.5

    win, lose, villain_folds, hero_folds = icm.confrontation_stacks(
        stacks, 0, 1, sit.pot, push, min(sit.to_call, sit.stack))

    cost = sit.orbit_cost()

    def pt(state):
        return icm.hero_points(icm.after_blinds(state, cost), pts)

    base = pt(hero_folds)
    push_value = (r.all_fold_prob * pt(villain_folds)
                  + r.call_prob * (r.equity_vs_call * pt(win)
                                   + (1 - r.equity_vs_call) * pt(lose)))
    r.push_points = push_value - base
    r.fold_points = 0.0

    if r.push_points > 0.3:
        r.recommend = "オールイン"
    elif r.push_points > 0:
        r.recommend = "オールイン(僅差)"
    else:
        r.recommend = "降りる"

    if sit.stack > 20:
        r.notes.append("スタックが20BBより深いので、"
                       "本来はオールイン一択ではなく通常のレイズも考える場面")
    if r.behind == 0:
        r.notes.append("後ろに誰もいないので、全員降りる確率は相手の判断次第")
    r.notes.append("降りても1周で %.1fBB は失う前提で比べている" % cost)
    r.notes.append("コールしてくるのは多くても1人とみなした近似")
    return r
