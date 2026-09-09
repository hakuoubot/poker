"""
よく出る局面をまとめて解いて貯めておく
====================================
    python precompute.py                  既定の組み合わせを全部
    python precompute.py --flops 6        フロップを6種類に絞る
    python precompute.py --stacks 20,40   スタックを指定
    python precompute.py --list           何をどれだけ解くかだけ見る

ソルバーの結果は `solver_engine/cache/` に貯まる(追記5参照)。
一度解いた設定は次から即答になるので、**寝ている間に解かせておく**のが狙い。
途中で止めても、解けたところまでは残る。もう一度走らせると
解いてある分は飛ばすので、そのまま続きから進む。

なぜフロップを「代表」で選ぶのか
--------------------------------
フロップは 22100 通りあるので全部は解けないし、解いても次に同じ board が
出る確率はほぼ無い。GTO の勉強で昔から使われている手は、
**テクスチャ(手触り)ごとの代表フロップを選んで、それだけ解く**というもの。
ここでもそれに倣って、ハイカード/ミドル/ロー・ペア・モノトーン・
繋がりの有無をひと通り含む代表フロップを並べてある。

自分がよく負ける局面が決まっているなら、REPRESENTATIVE_FLOPS を
そのボードに書き換えて使う方が効く。
"""

import argparse
import os
import sys
import time

import strategy as st
from cards import parse_cards

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "solver_engine"))
import solver_bridge as sv                                  # noqa: E402

# テクスチャの代表。左から順に「よく出る・重要」の順に並べてある。
REPRESENTATIVE_FLOPS = [
    ("Ah7d2c", "Aハイ・バラバラ"),
    ("Kh9s4d", "Kハイ・バラバラ"),
    ("Qs8d7h", "Qハイ・繋がりあり"),
    ("Jh9h5c", "Jハイ・2枚同じスート"),
    ("9s8d7c", "繋がった中位"),
    ("8h8d3c", "ペアボード"),
    ("Ts6s2s", "モノトーン"),
    ("6c5d4h", "低くて繋がる"),
    ("AhKd7s", "ブロードウェイ2枚"),
    ("Kd7d3d", "モノトーン・Kハイ"),
    ("Qh Qd 5s", "ペアボード・高い"),
    ("Th9d2c", "中位・少し繋がる"),
]

DEFAULT_STACKS = [20, 40, 80]
DEFAULT_SEATS = [("BTN", "BB"), ("CO", "BB"), ("BB", "CO")]


def build_situation(flop, hero, villain, stack, pot):
    return st.Situation(
        parse_cards("AsKs"), parse_cards(flop),
        hero_pos=hero, villain_pos=villain,
        pot=pot, to_call=0, stack=stack, iters=4000)


def spots(flops, stacks, seats):
    for board, label in flops:
        for stack in stacks:
            for hero, villain in seats:
                yield board, label, hero, villain, stack


def main():
    ap = argparse.ArgumentParser(description="よく出る局面を先に解いておく")
    ap.add_argument("--flops", type=int, default=len(REPRESENTATIVE_FLOPS),
                    help="使うフロップの数(既定: 全部)")
    ap.add_argument("--stacks", default=",".join(map(str, DEFAULT_STACKS)),
                    help="スタック(BB)をカンマ区切りで")
    ap.add_argument("--list", action="store_true", help="解かずに一覧だけ")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    flops = REPRESENTATIVE_FLOPS[:max(1, args.flops)]
    stacks = [float(x) for x in args.stacks.split(",") if x.strip()]
    todo = list(spots(flops, stacks, DEFAULT_SEATS))

    if sv.find_solver() is None:
        print("ソルバー本体がありません。solver_engine/README.md を見てください")
        return

    n, mb = sv.cache_info()
    print("解く局面: %d(フロップ%d x スタック%d x 席%d)"
          % (len(todo), len(flops), len(stacks), len(DEFAULT_SEATS)))
    print("いま貯まっている結果: %d件 %.1fMB" % (n, mb))
    print("フロップ1局面あたり 80秒前後。全部で %.1f 時間の見込み"
          % (len(todo) * 80 / 3600))
    if args.list:
        for board, label, hero, villain, stack in todo:
            print("  %-10s %-18s %s vs %s  %.0fBB"
                  % (board, label, hero, villain, stack))
        return

    print("Ctrl+C でいつでも止められます(解けた分は残ります)\n")
    t0 = time.perf_counter()
    solved = reused = failed = 0

    for i, (board, label, hero, villain, stack) in enumerate(todo, 1):
        pot = 2.1 if hero != "BB" else 5.0
        sit = build_situation(board, hero, villain, stack, pot)
        ip, oop, hero_is_ip = st.solver_ranges(sit)
        head = ("[%d/%d] %-10s %-18s %s vs %s %.0fBB"
                % (i, len(todo), board, label, hero, villain, stack))
        try:
            t1 = time.perf_counter()
            r = sv.solve_spot(sit.hole, sit.board, ip, oop, pot, stack,
                              hero_is_ip, False)
            dt = time.perf_counter() - t1
            if r.cached:
                reused += 1
                print(head + "  済み(再利用)")
            else:
                solved += 1
                print(head + "  %.0f秒 残り搾取率 %.2f%%"
                      % (dt, r.exploitability or -1))
        except KeyboardInterrupt:
            print("\n中断しました")
            break
        except Exception as exc:
            failed += 1
            print(head + "  失敗: %s" % exc)

    n, mb = sv.cache_info()
    print("\n新しく解いた %d / 済みだった %d / 失敗 %d"
          % (solved, reused, failed))
    print("かかった時間 %.1f分、保存 %d件 %.1fMB"
          % ((time.perf_counter() - t0) / 60, n, mb))


if __name__ == "__main__":
    main()
