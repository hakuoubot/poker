"""
アクションの優先度を出す
========================
麻雀版が「切る牌を期待値で並べる」のと同じ考え方で、
フォールド/コール/レイズ(またはチェック/ベット)を **期待値の大きい順** に並べる。

期待値の基準
------------
すでにポットに入っているお金は取り戻せない(サンクコスト)ので、
「降りる」を 0 として、そこからいくら増えるかで比べる。

  ポット P(相手のベット c を含む)に対して c をコールする場合
      EV(コール) = 勝率 x P - (1 - 勝率) x c

  ポット P に対して R まで上げる場合
      相手が降りる確率を f として
      EV(レイズ) = f x P + (1 - f) x { 勝率' x (P + R - c) - (1 - 勝率') x R }

  誰もベットしていない場面では
      EV(チェック) = 勝率 x P      (そのままショーダウンまで行くとみなす)

相手が降りる確率 f には MDF(ミニマム・ディフェンス・フリークエンシー)を使う。
「相手がセオリー通りに、降りすぎず守る」場合の値。
相手が降りやすい/降りにくいと感じるなら、この数字は割り引いて読む。

勝率' (相手が降りずに付いてきたときの勝率)は、
相手のレンジのうち **今のボードで強い方から (1-f) の割合** が続けてくると仮定して
計算し直したもの。ベットすると弱い手が降りて勝率が下がる、という効果を入れている。

分かっている近似(HANDOFF.md にも同じことを書いてある)
--------------------------------------------------
- 以降のストリートのベットを考えていない。インプライドオッズ
  (完成したときに余分に取れる分)は EV に入っていない。
  ドローハンドの EV は実際よりやや低めに出る。
- 相手のレンジは「そのポジションのオープンレンジ」で固定。
  フロップ以降で相手が降りていったぶんの絞り込みはしていない。
- 複数人相手のときは全員が同じレンジ・同じ確率で降りるとみなしている。
"""

from cards import rank_of, suit_of
import draws
import icm
import pushfold
import preflop as pf
from equity import equity, standard_error
from evaluator import evaluate

STREETS = ("プリフロップ", "フロップ", "ターン", "リバー")


def street_of(board):
    n = len(board)
    if n == 0:
        return "プリフロップ"
    if n == 3:
        return "フロップ"
    if n == 4:
        return "ターン"
    if n == 5:
        return "リバー"
    raise ValueError("場のカードは0,3,4,5枚のいずれか(今は%d枚)" % n)


class Situation:
    """判断に必要な情報をまとめたもの。"""

    def __init__(self, hole, board=(), hero_pos="BTN", villain_pos="CO",
                 opponents=1, pot=3.0, to_call=0.0, stack=100.0,
                 iters=20000, use_range=True, seed=None,
                 tournament=False, points=None, villain_stack=None,
                 other_stacks=(), ante=0.0, villain_action="なし"):
        self.hole = list(hole)
        self.board = list(board)
        self.hero_pos = hero_pos
        self.villain_pos = villain_pos
        self.opponents = max(1, int(opponents))
        self.pot = float(pot)
        self.to_call = float(to_call)
        self.stack = float(stack)
        self.iters = int(iters)
        self.use_range = use_range
        self.seed = seed

        # トーナメント(ポーカーチェイスのランク戦)として順位期待値で判断するか
        self.tournament = tournament
        self.points = points
        self.villain_stack = (float(villain_stack)
                              if villain_stack is not None else float(stack))
        self.other_stacks = [float(x) for x in other_stacks]
        self.ante = float(ante)
        # 相手が前のストリートまでに見せた行動。レンジを絞るのに使う
        self.villain_action = villain_action

    def orbit_cost(self):
        """1周(全員に1回ずつ手番が回る間)に自分が払うブラインドとアンティ。

        ターボはこれが効くので、順位EVの計算では全部の結末にこれを掛けている。
        """
        players = 2 + len(self.other_stacks)
        return 1.5 + self.ante * players

    def table_stacks(self):
        """[自分, 戦う相手, その他...] のチップ量。ICM に渡す形。"""
        return [self.stack, self.villain_stack] + self.other_stacks

    @property
    def street(self):
        return street_of(self.board)


class Option:
    def __init__(self, name, ev, detail, kind="other", hero_total=0.0,
                 fold_prob=0.0, eq_continue=0.0):
        self.name = name
        self.ev = ev                    # チップ基準の期待値
        self.detail = detail
        self.kind = kind                # fold / call / passive / aggressive
        self.hero_total = hero_total    # 自分がこれから出す合計
        self.fold_prob = fold_prob      # 相手が降りる確率(攻める手のみ)
        self.eq_continue = eq_continue  # 付いてこられたときの勝率
        self.icm_ev = None              # 順位ポイント基準の期待値

    def __repr__(self):
        return "Option(%s, ev=%.2f)" % (self.name, self.ev)


class Analysis:
    def __init__(self):
        self.street = ""
        self.equity = None
        self.draw = None
        self.options = []
        self.notes = []
        self.chart = None          # プリフロップのレンジ表による推奨
        self.pot_odds = None       # 必要勝率(チップ基準)
        self.icm_pot_odds = None   # 必要勝率(ICM込み)
        self.finish = None         # 今の順位確率の説明
        self.pushfold = None       # 浅いスタックのオールイン判断
        self.spr = None

    @property
    def best(self):
        return self.options[0] if self.options else None


# ---------------------------------------------------------
# 相手のレンジ
# ---------------------------------------------------------
def chen_score(hole):
    """Chen フォーミュラ。プリフロップでレンジを強さ順に並べるための近似値。

    ポーカーで昔から使われている簡易な点数付け。ソルバーの順位とは
    完全には一致しないが、レンジの上澄みを取る用途には十分。
    """
    a, b = hole
    ra, rb = rank_of(a), rank_of(b)
    hi, lo = max(ra, rb), min(ra, rb)

    def base(r):
        if r == 12:
            return 10.0
        if r == 11:
            return 8.0
        if r == 10:
            return 7.0
        if r == 9:
            return 6.0
        return (r + 2) / 2.0

    score = base(hi)
    if ra == rb:
        score = max(score * 2, 5.0)
    else:
        gap = hi - lo - 1
        if gap == 0:
            pass
        elif gap == 1:
            score -= 1
        elif gap == 2:
            score -= 2
        elif gap == 3:
            score -= 4
        else:
            score -= 5
        if gap <= 1 and hi < 10:
            score += 1
    if suit_of(a) == suit_of(b):
        score += 2
    return score


def _strength_key(combo, board):
    """相手のレンジを強い順に並べるための値。"""
    if board:
        return evaluate(list(combo) + list(board))
    return chen_score(combo)


# 相手が前のストリートで見せた行動ごとの、レンジの絞り方。
# (強い方から残す割合, 弱い方から残す割合)
#
# 「降りずに続けた」という情報でレンジは狭くなる。これを入れないと、
# フロップで降りなかった相手をプリフロップのレンジのまま扱ってしまう。
#
# コールは強い方から素直に狭くなる(降りるのは弱い手)。
# ベットは違う。**強い手とブラフの二極**になるので、
# 上から40%のように取ると相手が全部本物になってしまい、
# 自分の勝率が実際よりずっと低く出る(実測で 31% -> 19% まで下がった)。
VILLAIN_ACTION_KEEP = {
    "なし": (1.0, 0.0),
    "コールしてきた": (0.60, 0.0),
    "打ってきた": (0.28, 0.14),
}


def _narrow(combos, board, value_frac, bluff_frac):
    """レンジを、強い方から value_frac ぶんと弱い方から bluff_frac ぶんに絞る。"""
    if value_frac >= 1.0 and bluff_frac <= 0:
        return combos
    ordered = sorted(combos, key=lambda c: _strength_key(c, board),
                     reverse=True)
    n = len(ordered)
    top = ordered[:max(1, int(round(n * value_frac)))]
    bottom = ordered[n - int(round(n * bluff_frac)):] if bluff_frac > 0 else []
    seen = set()
    out = []
    for c in top + bottom:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def villain_range(sit):
    """相手が持っていそうな2枚の組み合わせ。use_range=False なら None。"""
    if not sit.use_range:
        return None
    dead = sit.hole + sit.board
    spec = pf.opponent_range(sit.villain_pos, aggressive=sit.to_call > 0)
    combos = pf.range_combos(spec, dead=dead)
    if not combos:
        return None

    value_frac, bluff_frac = VILLAIN_ACTION_KEEP.get(
        sit.villain_action, (1.0, 0.0))
    if sit.board and (value_frac < 1.0 or bluff_frac > 0):
        combos = _narrow(combos, sit.board, value_frac, bluff_frac)
    return combos or None


def continuing_range(combos, board, fold_prob):
    """ベットに対して降りずに続ける部分(強い方から)。"""
    if combos is None:
        return None
    keep = max(1, int(round(len(combos) * (1.0 - fold_prob))))
    ordered = sorted(combos, key=lambda c: _strength_key(c, board),
                     reverse=True)
    return ordered[:keep]


# ---------------------------------------------------------
# 本体
# ---------------------------------------------------------
def analyze(sit, progress=None, should_stop=None):
    a = Analysis()
    a.street = sit.street
    a.draw = draws.analyze(sit.hole, sit.board)

    combos = villain_range(sit)
    if combos is None and sit.use_range:
        a.notes.append("相手のレンジが自分のカードと全部かぶるため"
                       "ランダム相手として計算した")

    # ステップ数を数えて進捗を出す(基準の勝率 + 攻めの手の勝率)
    sizes = _aggressive_sizes(sit)
    total_steps = 1 + len(sizes)
    step = [0]

    def sub_progress(frac):
        if progress:
            progress((step[0] + frac) / total_steps)

    base = equity(sit.hole, sit.board, opponents=sit.opponents,
                  opp_range=combos, iters=sit.iters, seed=sit.seed,
                  progress=sub_progress, should_stop=should_stop)
    a.equity = base
    step[0] = 1

    eq = base.equity
    P = sit.pot
    c = min(sit.to_call, sit.stack)

    if c > 0:
        a.pot_odds = c / (P + c) if (P + c) > 0 else 0.0
    a.spr = (sit.stack / P) if P > 0 else None

    options = []
    if c > 0:
        options.append(Option("フォールド", 0.0,
                              ["ここで降りれば増減なし(すでに入れた分は戻らない)"],
                              kind="fold"))
        ev_call = eq * P - (1 - eq) * c
        options.append(Option(
            "コール", ev_call,
            ["勝率 %.1f%% x %.1f - 負け %.1f%% x %.1f" %
             (eq * 100, P, (1 - eq) * 100, c),
             "必要勝率(ポットオッズ) %.1f%% に対して %.1f%%"
             % (a.pot_odds * 100, eq * 100)],
            kind="call", hero_total=c))
    elif a.street == "プリフロップ":
        options.append(Option(
            "上げない(降りる/チェック)", eq * P,
            ["レイズせずに進んだ場合の目安",
             "勝率 %.1f%% x ポット %.1f" % (eq * 100, P)], kind="passive"))
    else:
        options.append(Option(
            "チェック", eq * P,
            ["ただでショーダウンまで行けるとみなした値",
             "勝率 %.1f%% x ポット %.1f" % (eq * 100, P)], kind="passive"))

    for label, amount in sizes:
        if should_stop and should_stop():
            break
        opt = _aggressive_option(sit, label, amount, eq, combos,
                                 sub_progress, should_stop)
        step[0] += 1
        options.append(opt)

    if sit.tournament:
        _apply_icm(a, sit, options, eq)
        options.sort(key=lambda o: o.icm_ev, reverse=True)
    else:
        options.sort(key=lambda o: o.ev, reverse=True)
    a.options = options

    if (sit.tournament and sit.street == "プリフロップ"
            and sit.to_call <= 0 and sit.stack <= 25):
        a.pushfold = pushfold.advise(sit)

    _add_notes(a, sit, base)
    if sit.street == "プリフロップ":
        a.chart = _chart_advice(sit)
        a.notes.append("プリフロップの期待値はあくまで参考。"
                       "実戦の基準はレンジ表(上の推奨)の方")
    return a


def _apply_icm(a, sit, options, eq):
    """チップの期待値を順位ポイントの期待値に置き換える。

    トーナメントは順位でポイントが決まるので、チップが増える手が
    そのまま得とは限らない。特に短スタックの終盤では、
    チップEVがプラスでも順位EVがマイナスになる勝負がよくある。
    """
    stacks = sit.table_stacks()
    pts = sit.points
    c = min(sit.to_call, sit.stack)
    cost = sit.orbit_cost()
    a.finish = icm.describe(stacks, pts)

    # 勝負相手の席。相手が複数なら、その人数ぶん席を使う
    villains = list(range(1, min(1 + sit.opponents, len(stacks))))
    if not villains:
        villains = [1]

    def pt(state):
        # どの結末でも、この後1周ぶんのブラインドは払う。
        # ICM は非線形なので、これを入れると短いスタックほど
        # 「待つ」ことの損が正しく効く
        return icm.hero_points(icm.after_blinds(state, cost), pts)

    def states(hero_total):
        """(勝ち, 負けの平均をとる関数, 相手が全員降りる, 自分が降りる)。"""
        w, loses, hf = icm.multi_outcomes(
            stacks, 0, villains, sit.pot, hero_total, c)
        vf = list(stacks)
        vf[0] = stacks[0] + sit.pot
        lose_pt = sum(pt(x) for x in loses) / len(loses)
        return pt(w), lose_pt, pt(vf), pt(hf)

    for o in options:
        w_pt, l_pt, vf_pt, base = states(o.hero_total)
        if o.kind == "fold":
            o.icm_ev = 0.0
        elif o.kind in ("call", "passive"):
            o.icm_ev = (eq * w_pt + (1 - eq) * l_pt) - base
        else:
            f, e2 = o.fold_prob, o.eq_continue
            o.icm_ev = (f * vf_pt
                        + (1 - f) * (e2 * w_pt + (1 - e2) * l_pt)) - base
        o.detail = o.detail + ["順位ポイントの期待値 %+.2f pt" % o.icm_ev]

    if c > 0:
        w_pt, l_pt, _vf, hf_pt = states(c)
        if abs(w_pt - l_pt) > 1e-12:
            a.icm_pot_odds = (hf_pt - l_pt) / (w_pt - l_pt)
        if a.icm_pot_odds is not None:
            a.notes.append(
                "ICM込みの必要勝率 %.1f%%(チップ基準だと %.1f%%)。"
                "順位を守るぶん、チップだけで見るより高い勝率が要る"
                % (a.icm_pot_odds * 100, (a.pot_odds or 0) * 100))
    a.notes.append("今の順位確率: " + a.finish)
    a.notes.append("降りても1周で %.1fBB(ブラインドとアンティ)は失う。"
                   "その分を織り込んで順位EVを出している" % cost)
    if len(villains) > 1:
        a.notes.append("相手%d人ぶんのチップの動きを順位EVに入れている"
                       "(誰が取るかは均等とみなす近似)" % len(villains))
    a.notes.append("ICMの前提: 実力差とブラインドの上がる速さは見ていない")


def _aggressive_sizes(sit):
    """攻める手の候補。(表示名, 自分が出す合計額)。"""
    P, c, stack = sit.pot, min(sit.to_call, sit.stack), sit.stack
    preflop = sit.street == "プリフロップ"
    out = []
    if preflop and c <= 0:
        # プリフロップで誰も上げていない場面。ポットの割合ではなく
        # 実戦の標準的なオープンサイズ(2.5BB)で見る。
        first = min(stack, 2.5)
        out.append(("オープンレイズ(%.1f まで)" % first, first))
        if stack > first * 1.5:
            out.append(("オールイン(%.1f)" % stack, stack))
        return out
    if c > 0:
        raise_to = min(stack, c * 3.0)
        if raise_to > c:
            name = "3ベット" if preflop else "レイズ"
            out.append(("%s(%.1f まで)" % (name, raise_to), raise_to))
        if stack > raise_to * 1.2:
            out.append(("オールイン(%.1f)" % stack, stack))
    else:
        half = min(stack, P * 0.5)
        full = min(stack, P)
        if half > 0:
            out.append(("ベット(1/2ポット %.1f)" % half, half))
        if full > half:
            out.append(("ベット(ポット %.1f)" % full, full))
        if stack > full * 1.5:
            out.append(("オールイン(%.1f)" % stack, stack))
    return out


def _aggressive_option(sit, label, amount, eq_base, combos,
                       progress, should_stop):
    P = sit.pot
    c = min(sit.to_call, sit.stack)
    R = amount

    # 相手が降りる確率(MDF)。相手が追加で払う額 / 払った後のポット
    extra = R - c
    denom = P + R
    f_one = extra / denom if denom > 0 else 0.0
    f_one = min(max(f_one, 0.0), 0.95)
    f_all = f_one ** sit.opponents

    cont = continuing_range(combos, sit.board, f_one)
    if cont is None:
        eq2 = eq_base
        eq_note = "相手をランダムとみなしているので勝率は変えていない"
    else:
        r2 = equity(sit.hole, sit.board, opponents=sit.opponents,
                    opp_range=cont, iters=max(4000, sit.iters // 2),
                    seed=sit.seed, progress=progress, should_stop=should_stop)
        eq2 = r2.equity
        eq_note = ("付いてこられた場合の勝率 %.1f%%(弱い手が降りるぶん%s)"
                   % (eq2 * 100,
                      "下がる" if eq2 < eq_base else "上がる"))

    ev = f_all * P + (1 - f_all) * (eq2 * (P + R - c) - (1 - eq2) * R)
    detail = [
        "相手が降りる確率 %.0f%%(MDF基準%s)"
        % (f_all * 100,
           " / %d人全員" % sit.opponents if sit.opponents > 1 else ""),
        eq_note,
        "降ろせば +%.1f、付いてこられたら 勝ち +%.1f / 負け -%.1f"
        % (P, P + R - c, R),
    ]
    return Option(label, ev, detail, kind="aggressive", hero_total=R,
                  fold_prob=f_all, eq_continue=eq2)


def _add_notes(a, sit, base):
    se = standard_error(base)
    if base.exact:
        a.notes.append("勝率は全%d通りを数え上げた正確な値" % base.samples)
    else:
        a.notes.append("勝率は %d回の試行(誤差 ±%.1f%% 程度)"
                       % (base.samples, se * 200))
    if sit.use_range:
        value_frac, bluff_frac = VILLAIN_ACTION_KEEP.get(
            sit.villain_action, (1.0, 0.0))
        if sit.board and (value_frac < 1.0 or bluff_frac > 0):
            note = ("相手のレンジは %s のオープンレンジのうち、"
                    "「%s」ぶん強い方から %.0f%%"
                    % (sit.villain_pos, sit.villain_action, value_frac * 100))
            if bluff_frac > 0:
                note += " + ブラフとして弱い方から %.0f%%" % (bluff_frac * 100)
            a.notes.append(note + " に絞ってある")
        else:
            a.notes.append("相手のレンジは %s のオープンレンジ相当とみなしている"
                           % sit.villain_pos)
    else:
        a.notes.append("相手を完全ランダムとして計算(実戦より自分に甘く出る)")

    if a.spr is not None and a.spr < 3 and sit.street != "プリフロップ":
        a.notes.append("SPR %.1f(スタックが浅い)。強い手なら一気に入れて良い場面"
                       % a.spr)

    if a.draw and a.draw.out_count and sit.street in ("フロップ", "ターン"):
        n = a.draw.out_count
        mult = 4 if sit.street == "フロップ" else 2
        a.notes.append("アウツ %d枚。ざっくり %d%% で伸びる(%d倍の法則)"
                       % (n, n * mult, mult))

    if any(o.name.startswith("オールイン") for o in a.options):
        a.notes.append("大きいベットの評価は「相手がMDF通りに降りる」前提。"
                       "実戦では大きく行くほど降りずに強い手だけ来ることが多いので、"
                       "オールインのEVは高めに出やすい")

    if a.pot_odds is not None:
        diff = base.equity - a.pot_odds
        if diff > 0:
            a.notes.append("勝率が必要勝率を %.1f ポイント上回っている"
                           % (diff * 100))
        else:
            a.notes.append("勝率が必要勝率に %.1f ポイント足りない"
                           % (-diff * 100))


def _chart_advice(sit):
    """プリフロップはレンジ表の推奨も併記する(こちらが実戦の基準)。"""
    if sit.to_call > 0:
        action, note = pf.vs_raise_action(sit.hero_pos, sit.villain_pos,
                                          sit.hole)
    else:
        action, note = pf.rfi_action(sit.hero_pos, sit.hole)
    return ("%s(%s)" % (pf.hand_class(sit.hole), action), note)


# ---------------------------------------------------------
# ソルバー(TexasSolver)に渡すレンジ
# ---------------------------------------------------------
def solver_ranges(sit):
    """(IPのレンジ, OOPのレンジ, 自分がIPか) を返す。

    ソルバーは両者のレンジが要る。ここでは equity の計算で使っているのと
    同じ考え方(ポジションのオープンレンジ相当)でレンジを決めている。
    自分の手札がレンジから漏れると戦略が引けないので、必ず足しておく。
    """
    hero_spec = pf.RFI.get(sit.hero_pos) or ""
    if sit.to_call > 0 or not hero_spec:
        three, call = pf.VS_RAISE.get(
            (pf.raiser_group(sit.villain_pos),
             pf.is_in_position(sit.hero_pos, sit.villain_pos)),
            pf.VS_RAISE[(pf.raiser_group(sit.villain_pos), False)])
        hero_spec = three + ", " + call
    hero = pf.expand(hero_spec) | {pf.hand_class(sit.hole)}
    villain = pf.expand(pf.opponent_range(sit.villain_pos,
                                          aggressive=sit.to_call > 0))

    hero_is_ip = pf.is_in_position(sit.hero_pos, sit.villain_pos)
    hero_text = ",".join(sorted(hero))
    villain_text = ",".join(sorted(villain))
    if hero_is_ip:
        return hero_text, villain_text, True
    return villain_text, hero_text, False
