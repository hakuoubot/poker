"""
TexasSolver(GTOソルバー)との橋渡し
==================================
麻雀版の `akochan_engine/mjai_bridge.py` と同じ役割。
「本物のソルバーに解かせて、こちらのルール層は根拠を語る」ための接続部分。

ソルバー本体はこのリポジトリに同梱しない(AGPL-3.0 で、こちらのコードとは別物)。
`solver_engine/README.md` の手順で置く。置かれていなくてもアプリは動く
(その場合 `find_solver()` が None を返す)。

TexasSolver はポストフロップ専用。プリフロップは preflop.py のチャートのまま。

呼び出しの流れ
--------------
1. 局面とレンジからコマンドファイルを組み立てる
2. `console_solver.exe -i そのファイル` を子プロセスで実行(カレントはソルバーのフォルダ)
3. 出てきた JSON の木をたどって、自分の手札の行動頻度を取り出す

外部への通信はしない。すべてローカルのプロセス呼び出し。
"""

import glob
import hashlib
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from cards import card_code                                    # noqa: E402

WORK_DIR = os.path.join(_HERE, "work")
CACHE_DIR = os.path.join(_HERE, "cache")

# 解いた結果を貯めておく。ソルバーの答えは
# (ボード・両者のレンジ・ポット・スタック・ベットサイズ・精度) だけで決まり、
# **自分の手札が何かには依存しない**。同じ設定なら手札を変えても
# 同じ木を引き直せるので、キャッシュが効く場面は多い。
CACHE_ENABLED = True

# ソルバーが1局面にかける時間の目安(実測)。GUI の待ち時間表示に使う。
# フロップが重いのはソルバー側の相場(公式ベンチでも172秒)。
# 枝を削って82秒まで縮めたが、これ以上は答えが変わるので削らない。
EXPECTED_SECONDS = {"フロップ": 85, "ターン": 10, "リバー": 8}


class SolverMissing(Exception):
    """ソルバー本体が置かれていない。"""


class SolverCancelled(Exception):
    """中止ボタンなどで計算を打ち切った。"""


def cache_path(commands):
    key = hashlib.sha1(commands.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, key + ".json")


def cache_info():
    """貯まっている件数と合計サイズ(MB)。"""
    if not os.path.isdir(CACHE_DIR):
        return 0, 0.0
    files = glob.glob(os.path.join(CACHE_DIR, "*.json"))
    size = sum(os.path.getsize(f) for f in files)
    return len(files), size / (1024 * 1024)


def clear_cache():
    n = 0
    for f in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        os.remove(f)
        n += 1
    return n


class SolverResult:
    def __init__(self, actions, freqs, node, seconds, exploitability,
                 cached=False):
        self.actions = actions          # ["CHECK", "BET 3.0"] のような行動名
        self.freqs = freqs              # 各行動の頻度(合計1)
        self.node = node                # どのノードの戦略か(表示用)
        self.seconds = seconds
        self.exploitability = exploitability
        self.cached = cached

    def best(self):
        i = max(range(len(self.freqs)), key=lambda k: self.freqs[k])
        return self.actions[i], self.freqs[i]

    def as_lines(self):
        out = []
        for name, f in sorted(zip(self.actions, self.freqs),
                              key=lambda x: -x[1]):
            out.append("%-16s %5.1f%%" % (translate_action(name), f * 100))
        return out


def translate_action(name):
    """ソルバーの行動名を日本語にする。"""
    if name.startswith("BET"):
        return "ベット %s" % _amount(name)
    if name.startswith("RAISE"):
        return "レイズ %s" % _amount(name)
    if name.startswith("DONK"):
        return "ドンクベット %s" % _amount(name)
    return {"CHECK": "チェック", "CALL": "コール",
            "FOLD": "フォールド", "ALLIN": "オールイン"}.get(name, name)


def _amount(name):
    try:
        return "%.1f" % float(name.split()[1])
    except (IndexError, ValueError):
        return ""


# ---------------------------------------------------------
# ソルバー本体を探す
# ---------------------------------------------------------
def find_solver():
    """console_solver の実行ファイルのパス。見つからなければ None。"""
    names = ("console_solver.exe", "console_solver")
    for pattern in ("TexasSolver*", "*"):
        for d in sorted(glob.glob(os.path.join(_HERE, pattern))):
            if not os.path.isdir(d):
                continue
            for n in names:
                p = os.path.join(d, n)
                if os.path.isfile(p):
                    return p
    return None


# ---------------------------------------------------------
# 入力ファイルの組み立て
# ---------------------------------------------------------
def range_text(classes):
    """{'AKs','TT'} -> 'AKs,TT'。ソルバーが読む形式。"""
    return ",".join(sorted(classes))


def build_commands(board, ip_range, oop_range, pot, stack,
                   bet_pct=50, raise_pct=60, threads=None,
                   accuracy=1.0, max_iteration=60, dump_rounds=1,
                   out_name="result.json"):
    """ソルバーに渡すコマンド列を組み立てる。"""
    if len(board) not in (3, 4, 5):
        raise ValueError("ソルバーはフロップ以降のみ(場のカードが3〜5枚)")
    if threads is None:
        threads = max(1, (os.cpu_count() or 4))

    # 残りのストリートぶんだけベットサイズを指定する
    streets = ["flop", "turn", "river"][len(board) - 3:]
    lines = [
        "set_pot %g" % pot,
        "set_effective_stack %g" % stack,
        "set_board %s" % ",".join(card_code(c) for c in board),
        "set_range_ip %s" % ip_range,
        "set_range_oop %s" % oop_range,
    ]
    # 木を小さく保つ。ベットサイズは1種類、レイズは今のストリートだけ。
    # 全ストリートにレイズを入れるとフロップの計算時間が4〜5倍になる
    # (実測: 68秒 -> 269秒)。反復を減らして誤魔化すと答えそのものが変わるので、
    # 削るなら枝の方を削る。
    for who in ("oop", "ip"):
        for i, street in enumerate(streets):
            lines.append("set_bet_sizes %s,%s,bet,%d" % (who, street, bet_pct))
            if i == 0:
                lines.append("set_bet_sizes %s,%s,raise,%d"
                             % (who, street, raise_pct))
    lines += [
        "set_allin_threshold 1.0",
        "build_tree",
        "set_thread_num %d" % threads,
        "set_accuracy %g" % accuracy,
        "set_max_iteration %d" % max_iteration,
        "set_print_interval 10",
        "set_use_isomorphism 1",
        "start_solve",
        "set_dump_rounds %d" % dump_rounds,
        "dump_result %s" % out_name,
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------
# 実行
# ---------------------------------------------------------
def run_solver(commands, out_name, timeout=600, should_stop=None):
    """ソルバーを実行して、出力 JSON を読み込んで返す。

    同じ設定を解いた結果が残っていればそれを使う(数十秒 -> 一瞬)。
    """
    if CACHE_ENABLED:
        path = cache_path(commands)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                return (saved["tree"], 0.0, saved.get("exploitability"), True)
            except (ValueError, KeyError):
                os.remove(path)      # 壊れていたら捨てて解き直す

    exe = find_solver()
    if exe is None:
        raise SolverMissing("ソルバー本体が見つからない。"
                            "solver_engine/README.md の手順で置いてください")
    solver_dir = os.path.dirname(exe)
    os.makedirs(WORK_DIR, exist_ok=True)

    in_path = os.path.join(WORK_DIR, "input.txt")
    with open(in_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(commands)

    out_path = os.path.join(solver_dir, out_name)
    if os.path.exists(out_path):
        os.remove(out_path)

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [exe, "-i", os.path.relpath(in_path, solver_dir)],
        cwd=solver_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, errors="replace")

    tail = []
    try:
        for line in proc.stdout:
            tail.append(line.rstrip())
            if len(tail) > 40:
                tail.pop(0)
            if should_stop and should_stop():
                proc.kill()
                raise SolverCancelled("計算を中止した")
            if time.perf_counter() - t0 > timeout:
                proc.kill()
                raise TimeoutError("ソルバーが %d 秒で終わらなかった" % timeout)
    finally:
        proc.stdout.close()
        proc.wait()

    seconds = time.perf_counter() - t0
    if not os.path.exists(out_path):
        raise RuntimeError("ソルバーが結果を出力しなかった:\n"
                           + "\n".join(tail[-10:]))
    with open(out_path, encoding="utf-8") as f:
        tree = json.load(f)
    expl = _last_exploitability(tail)

    if CACHE_ENABLED:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path(commands), "w", encoding="utf-8") as f:
            json.dump({"tree": tree, "exploitability": expl,
                       "seconds": seconds, "commands": commands}, f)
    return tree, seconds, expl, False


def _last_exploitability(lines):
    for line in reversed(lines):
        if "Total exploitability" in line:
            for tok in line.split():
                try:
                    return float(tok)
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------
# 出力の木から自分の手の戦略を取り出す
# ---------------------------------------------------------
def hand_key(hole):
    """カード2枚 -> ソルバーの表記 'AsKh'(ランク降順、同ランクはスート降順)。"""
    a, b = sorted(hole, reverse=True)
    return card_code(a) + card_code(b)


def pick_node(tree, path):
    """['CHECK', 'BET'] のような道順で子をたどる。BET/RAISE は前方一致。"""
    node = tree
    walked = []
    for want in path:
        children = node.get("childrens") or {}
        hit = None
        for name in children:
            if name == want or name.startswith(want):
                hit = name
                break
        if hit is None:
            raise KeyError("ソルバーの木に %s が無い(あるのは %s)"
                           % (want, list(children)))
        node = children[hit]
        walked.append(hit)
    return node, walked


def strategy_for(node, hole):
    """ノードから自分の手札の行動頻度を取り出す。"""
    strat = node.get("strategy")
    if not strat:
        raise KeyError("そのノードに戦略が入っていない")
    actions = strat["actions"]
    table = strat["strategy"]
    key = hand_key(hole)
    if key not in table:
        raise KeyError("自分の手 %s がソルバーに渡したレンジに入っていない" % key)
    return actions, table[key]


def node_path_for(hero_is_ip, facing_bet):
    """よくある4つの局面に対応する木の道順。

    ソルバーの木の根は必ず OOP(先に行動する側)の判断から始まる。
    """
    if not hero_is_ip:
        # 自分が先に動く側
        return [] if not facing_bet else ["CHECK", "BET"]
    # 自分は後に動く側。相手がチェックしたか、ベットしたか
    return ["BET"] if facing_bet else ["CHECK"]


def solve_spot(hole, board, ip_range, oop_range, pot, stack,
               hero_is_ip, facing_bet, timeout=600, should_stop=None,
               **kwargs):
    """局面を解いて、自分の手札の行動頻度を返す。"""
    out_name = "poker_advisor_result.json"
    cmds = build_commands(board, ip_range, oop_range, pot, stack,
                          out_name=out_name, **kwargs)
    tree, seconds, expl, cached = run_solver(cmds, out_name, timeout=timeout,
                                             should_stop=should_stop)
    node, walked = pick_node(tree, node_path_for(hero_is_ip, facing_bet))
    actions, freqs = strategy_for(node, hole)
    where = " → ".join(translate_action(w) for w in walked) or "先頭(相手より先に行動)"
    return SolverResult(actions, freqs, where, seconds, expl, cached)


# ---------------------------------------------------------
# 自己テスト
# ---------------------------------------------------------
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from cards import parse_cards

    exe = find_solver()
    print("ソルバー:", exe or "(見つからない)")
    if exe is None:
        print("solver_engine/README.md の手順で置いてください")
        raise SystemExit(1)

    hole = parse_cards("JsTs")
    board = parse_cards("Qs8d7h")
    print("局面: JsTs / Qs8d7h  自分=IP、相手がチェック")
    r = solve_spot(hole, board,
                   ip_range="JTs,AA,KK,QQ,AKs,AQs,KQs,99,88,77",
                   oop_range="AA,KK,QQ,JJ,TT,AQs,AJs,KQs,QJs,JTs,T9s,98s,87s",
                   pot=6, stack=97, hero_is_ip=True, facing_bet=False,
                   max_iteration=40)
    print("ノード:", r.node)
    for line in r.as_lines():
        print("  " + line)
    print("%.1f秒 / 残り搾取率 %.2f%%" % (r.seconds, r.exploitability or 0))
