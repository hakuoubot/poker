"""
ICM(順位期待値)の検証
======================
    python test_icm.py

ICM には確かめられる性質がいくつかあるので、それを使って実装を検証する。
勝率と違って「公表されている正解の数値」が手に入りにくいため、
数値そのものではなく **満たすべき性質** を見ている。
"""

import random

import icm

PTS = icm.POINT_PRESETS["ポーカーチェイス STAGE V"]


def check(name, ok, extra=""):
    print("  %s%s%s" % ("OK " if ok else "NG ", name,
                        ("  " + extra) if extra else ""))
    return 0 if ok else 1


def main():
    bad = 0
    print("順位確率の性質:")

    p = icm.finish_probabilities([100] * 6, 0)
    bad += check("全員同じスタックなら各順位が均等",
                 all(abs(x - 1 / 6) < 1e-9 for x in p))

    rng = random.Random(4)
    worst = 0.0
    for _ in range(200):
        stacks = [rng.randint(1, 200) for _ in range(6)]
        s = sum(icm.finish_probabilities(stacks, 0))
        worst = max(worst, abs(s - 1.0))
    bad += check("確率の合計が1(ランダム200通り)", worst < 1e-9,
                 "最大ずれ %.2e" % worst)

    p = icm.finish_probabilities([600, 0, 0, 0, 0, 0], 0)
    bad += check("1人が全チップなら必ず1位", abs(p[0] - 1.0) < 1e-9)

    stacks = [50, 30, 20, 20, 20, 20]
    p = icm.finish_probabilities(stacks, 0)
    bad += check("1位になる確率はチップの比率と一致",
                 abs(p[0] - 50 / sum(stacks)) < 1e-9)

    print("順位ポイントの性質:")
    a = icm.hero_points([60, 30, 30, 30, 30, 30], PTS)
    b = icm.hero_points([30, 30, 30, 30, 30, 30], PTS)
    c = icm.hero_points([10, 30, 30, 30, 30, 30], PTS)
    bad += check("チップが多いほど期待ポイントも高い", a > b > c,
                 "%.2f > %.2f > %.2f" % (a, b, c))

    # ICM の肝。チップを倍にしても期待ポイントは倍にならない(増え方が鈍る)
    base = icm.hero_points([20, 40, 40, 40, 40, 40], PTS)
    doubled = icm.hero_points([40, 40, 40, 40, 40, 40], PTS)
    zero = icm.hero_points([0.0001, 40, 40, 40, 40, 40], PTS)
    gain = doubled - base
    loss = base - zero
    bad += check("勝って増える価値より、負けて失う価値の方が大きい",
                 loss > gain, "失う %.2f > 得る %.2f" % (loss, gain))

    print("必要勝率:")
    stacks = [12, 20, 25, 30, 28, 35]
    for pot, call in ((6, 4), (10, 5), (20, 20)):
        w, l, _vf, hf = icm.confrontation_stacks(stacks, 0, 1, pot, call, call)
        req = icm.required_equity(w, l, hf, PTS)
        chip = call / (pot + call)
        bad += check("ポット%d コール%d: ICM必要勝率 > チップ基準" % (pot, call),
                     req > chip, "%.1f%% > %.1f%%" % (req * 100, chip * 100))

    print("マルチウェイ:")
    stacks = [20, 25, 30, 28, 35, 22]
    w, loses, hf = icm.multi_outcomes(stacks, 0, [1, 2], 6, 10, 4)
    total = sum(stacks) + 6
    bad += check("複数人でもチップの総量が変わらない",
                 all(abs(sum(x) - total) < 1e-9 for x in [w, hf] + loses))

    w1, _l1, _h1 = icm.multi_outcomes(stacks, 0, [1], 6, 10, 4)
    bad += check("相手が多いほど、勝ったときの期待ポイントも高い",
                 icm.hero_points(w, PTS) > icm.hero_points(w1, PTS),
                 "%.2f > %.2f" % (icm.hero_points(w, PTS),
                                  icm.hero_points(w1, PTS)))

    single = icm.confrontation_stacks(stacks, 0, 1, 6, 10, 4)
    multi = icm.multi_outcomes(stacks, 0, [1], 6, 10, 4)
    bad += check("相手1人なら2人用の計算と一致",
                 single[0] == multi[0] and single[1] == multi[1][0])

    print("ブラインドコスト:")
    before = icm.hero_points([10, 30, 30, 30, 30, 30], PTS)
    after = icm.hero_points(icm.after_blinds([10, 30, 30, 30, 30, 30], 2.1),
                            PTS)
    bad += check("1周待つと期待ポイントは下がる", after < before,
                 "%.2f -> %.2f" % (before, after))
    short = (icm.hero_points([8, 40, 40, 40, 40, 40], PTS)
             - icm.hero_points(icm.after_blinds([8, 40, 40, 40, 40, 40], 2.1),
                               PTS))
    deep = (icm.hero_points([60, 40, 40, 40, 40, 40], PTS)
            - icm.hero_points(icm.after_blinds([60, 40, 40, 40, 40, 40], 2.1),
                              PTS))
    bad += check("同じ2.1BBでも短いスタックの方が痛い", short > deep,
                 "%.2f > %.2f" % (short, deep))

    print("チップ保存:")
    w, l, vf, hf = icm.confrontation_stacks(stacks, 0, 1, 6, 10, 4)
    total = sum(stacks) + 6
    bad += check("どの結末でもチップの総量が変わらない",
                 all(abs(sum(x) - total) < 1e-9 for x in (w, l, vf, hf)))

    print("結果:", "OK" if bad == 0 else "NG %d件" % bad)


if __name__ == "__main__":
    main()
