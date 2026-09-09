"""
Nash プッシュ/フォールドの検証
==============================
    python test_nash.py

`equity_matrix.json` が必要(先に `python equity_matrix.py`)。

解が正しいかを、**満たすべき性質**と**公表されている値の範囲**の両方で見る。
ポーカーのプッシュ/フォールド表はチップEV基準なら値がほぼ確定しているので、
まずそちらを再現できるか確かめてから ICM に切り替える。
"""

import sys

import icm
import nash


def check(name, ok, extra=""):
    print("  %s%s%s" % ("OK " if ok else "NG ", name,
                        ("  " + extra) if extra else ""))
    return 0 if ok else 1


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if nash.load() is None:
        print("equity_matrix.json がありません。"
              "先に python equity_matrix.py を実行してください")
        return

    bad = 0

    print("チップEV基準(公表されている表と比べられる):")
    table = nash.Table([10] * 6, points=None, ante=0.1)
    r = nash.solve(table)
    pct = {p: nash.range_percent(r["shove"][p]) for p in nash.ORDER[:-1]}
    print("  オールインする割合: "
          + " / ".join("%s %.0f%%" % (p, pct[p]) for p in nash.ORDER[:-1]))

    bad += check("後の席ほど広く押す(UTG < CO < SB)",
                 pct["UTG"] < pct["CO"] < pct["SB"],
                 "%.0f%% < %.0f%% < %.0f%%"
                 % (pct["UTG"], pct["CO"], pct["SB"]))
    bad += check("SB の10BBは 40〜80%% に収まる",
                 40 <= pct["SB"] <= 80, "%.0f%%" % pct["SB"])
    bad += check("UTG の10BBは 10〜30%% に収まる",
                 10 <= pct["UTG"] <= 30, "%.0f%%" % pct["UTG"])

    call_sb_bb = nash.range_percent(r["call"][("SB", "BB")])
    bad += check("BB のコールは SB の押しより狭い",
                 call_sb_bb < pct["SB"],
                 "コール %.0f%% < 押し %.0f%%" % (call_sb_bb, pct["SB"]))
    bad += check("BB のコールは 20〜55%% に収まる",
                 20 <= call_sb_bb <= 55, "%.0f%%" % call_sb_bb)

    print("スタックが深くなると狭くなるか:")
    deep = nash.Table([25] * 6, points=None, ante=0.1)
    rd = nash.solve(deep)
    deep_sb = nash.range_percent(rd["shove"]["SB"])
    bad += check("25BB の SB は 10BB より狭く押す",
                 deep_sb < pct["SB"],
                 "25BB %.0f%% < 10BB %.0f%%" % (deep_sb, pct["SB"]))

    print("順位ポイント(ICM)にすると:")
    pts = icm.POINT_PRESETS[icm.DEFAULT_PRESET]
    ricm = nash.solve(nash.Table([10] * 6, points=pts, ante=0.1))
    icm_sb = nash.range_percent(ricm["shove"]["SB"])
    icm_call = nash.range_percent(ricm["call"][("SB", "BB")])
    print("  SB の押し %.0f%% / BB のコール %.0f%%" % (icm_sb, icm_call))
    bad += check("ICM の方がコールが辛くなる(飛ぶと順位が確定するため)",
                 icm_call <= call_sb_bb + 0.5,
                 "ICM %.0f%% <= チップ %.0f%%" % (icm_call, call_sb_bb))

    print("結果:", "OK" if bad == 0 else "NG %d件" % bad)


if __name__ == "__main__":
    main()
