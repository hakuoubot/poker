"""
テキサスホールデム アクションアドバイザー GUI
============================================
自分の2枚と場のカードをクリックで入力すると、
フォールド/コール/レイズ(またはチェック/ベット)を期待値の高い順に並べて表示する。

    python app.py

金額はすべて BB(ビッグブラインド)単位で入力する。
6人リング(6-max)のキャッシュゲームを前提にしたレンジ表を使っている。

すべてローカルで完結し、外部への通信は一切行わない。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, font as tkfont

import draws
import icm
import preflop as pf
import ranges_gen
import strategy as st
from cards import (RANKS, SUITS, SUIT_SYMBOL, SUIT_COLOR, make_card,
                   card_label, card_color)
from equity import Cancelled

# GTOソルバー(TexasSolver)との橋渡し。用意できていなくてもアプリは動く。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "solver_engine"))
try:
    import solver_bridge as sv
except ImportError:
    sv = None

# ---------------------------------------------------------
# 見た目の定数
# ---------------------------------------------------------
BG = "#f4f4f2"
PANEL_BG = "#ffffff"
SLOT_EMPTY = "#e3e3df"
SLOT_FOCUS = "#ffe9a8"
USED_FG = "#b9b9b4"

WIN_COLOR = "#2e7d32"
TIE_COLOR = "#b8a300"
LOSE_COLOR = "#c62828"

# スロットの並び。(グループ名, スロット数)
SLOT_GROUPS = (("自分の手札", 2), ("フロップ", 3), ("ターン", 1), ("リバー", 1))
HOLE_SLOTS = 2
TOTAL_SLOTS = 7


def pick_font(root, size, bold=False):
    """日本語が出るフォントを選ぶ。無ければ tk の既定にまかせる。"""
    families = set(tkfont.families(root))
    for name in ("Meiryo UI", "Meiryo", "Yu Gothic UI", "MS UI Gothic"):
        if name in families:
            return tkfont.Font(family=name, size=size,
                               weight="bold" if bold else "normal")
    return tkfont.Font(size=size, weight="bold" if bold else "normal")


class App:
    def __init__(self, root):
        self.root = root
        root.title("テキサスホールデム アクションアドバイザー")
        root.configure(bg=BG)

        self.f_small = pick_font(root, 9)
        self.f_base = pick_font(root, 10)
        self.f_bold = pick_font(root, 10, bold=True)
        self.f_card = pick_font(root, 11, bold=True)
        self.f_big = pick_font(root, 15, bold=True)
        self.f_mono = tkfont.Font(family="Consolas", size=10)

        self.slots = [None] * TOTAL_SLOTS
        self.focus_index = 0
        self.slot_widgets = []
        self.card_buttons = {}

        # 計算スレッドとのやりとり
        self.queue = queue.Queue()
        self.generation = 0
        self.busy = False
        self.pending = False

        # ソルバー(別プロセス)。計算が長いので手動ボタンで動かす
        self.solver_gen = 0
        self.solver_busy = False
        self.solver_started = 0.0

        # レンジ表の生成(169ハンドぶん計算するので時間がかかる)
        self.chart_gen = 0
        self.chart_busy = False

        self._build()
        self._refresh_inputs()
        self.root.after(80, self._poll_queue)

    # -----------------------------------------------------
    # 画面の組み立て
    # -----------------------------------------------------
    def _build(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        left = tk.Frame(outer, bg=BG)
        left.pack(side="left", fill="y")
        right = tk.Frame(outer, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self._build_slots(left)
        self._build_palette(left)
        self._build_settings(left)
        self._build_tournament(left)
        self._build_results(right)

    def _build_slots(self, parent):
        box = tk.LabelFrame(parent, text="カード", bg=BG, font=self.f_bold,
                            padx=8, pady=6)
        box.pack(fill="x")

        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w")
        index = 0
        for group_name, count in SLOT_GROUPS:
            g = tk.Frame(row, bg=BG)
            g.pack(side="left", padx=(0, 10))
            tk.Label(g, text=group_name, bg=BG, font=self.f_small).pack()
            cards_row = tk.Frame(g, bg=BG)
            cards_row.pack()
            for _ in range(count):
                lbl = tk.Label(cards_row, text="", width=3, height=2,
                               font=self.f_card, bg=SLOT_EMPTY,
                               relief="ridge", bd=2)
                lbl.pack(side="left", padx=2)
                i = index
                lbl.bind("<Button-1>", lambda _e, k=i: self._focus_slot(k))
                lbl.bind("<Button-3>", lambda _e, k=i: self._clear_slot(k))
                self.slot_widgets.append(lbl)
                index += 1

        btns = tk.Frame(box, bg=BG)
        btns.pack(anchor="w", pady=(6, 0))
        tk.Button(btns, text="1枚戻す", font=self.f_base,
                  command=self._undo).pack(side="left")
        tk.Button(btns, text="全部消す", font=self.f_base,
                  command=self._clear_all).pack(side="left", padx=6)
        tk.Label(btns, text="スロットを右クリックで1枚消去",
                 bg=BG, font=self.f_small, fg="#777").pack(side="left",
                                                           padx=6)

    def _build_palette(self, parent):
        box = tk.LabelFrame(parent, text="カードを選ぶ", bg=BG,
                            font=self.f_bold, padx=8, pady=6)
        box.pack(fill="x", pady=(10, 0))

        grid = tk.Frame(box, bg=BG)
        grid.pack()
        for si, suit in enumerate(SUITS):
            tk.Label(grid, text=SUIT_SYMBOL[suit], bg=BG, font=self.f_card,
                     fg=SUIT_COLOR[suit]).grid(row=si, column=0, padx=(0, 4))
            for ri, rank in enumerate(RANKS):
                card = make_card(ri, si)
                b = tk.Button(grid, text=rank + SUIT_SYMBOL[suit], width=3,
                              font=self.f_base, fg=SUIT_COLOR[suit],
                              bg="white", relief="raised", padx=0, pady=0,
                              command=lambda c=card: self._place_card(c))
                b.grid(row=si, column=ri + 1, padx=1, pady=1)
                self.card_buttons[card] = b

    def _build_settings(self, parent):
        box = tk.LabelFrame(parent, text="状況(単位はすべて BB)", bg=BG,
                            font=self.f_bold, padx=8, pady=6)
        box.pack(fill="x", pady=(10, 0))

        self.var_hero = tk.StringVar(value="BTN")
        self.var_villain = tk.StringVar(value="CO")
        self.var_opponents = tk.StringVar(value="1")
        self.var_pot = tk.StringVar(value="6")
        self.var_call = tk.StringVar(value="4")
        self.var_stack = tk.StringVar(value="97")
        self.var_iters = tk.StringVar(value="20000")
        self.var_use_range = tk.BooleanVar(value=True)
        self.var_villain_action = tk.StringVar(value="なし")

        def add(row, col, text, widget):
            tk.Label(box, text=text, bg=BG, font=self.f_base).grid(
                row=row, column=col * 2, sticky="e", padx=(0, 4), pady=3)
            widget.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 16))

        hero = ttk.Combobox(box, textvariable=self.var_hero, width=6,
                            state="readonly", values=list(pf.POSITIONS))
        villain = ttk.Combobox(box, textvariable=self.var_villain, width=6,
                               state="readonly", values=list(pf.POSITIONS))
        opponents = ttk.Combobox(box, textvariable=self.var_opponents,
                                 width=6, state="readonly",
                                 values=["1", "2", "3", "4", "5"])
        add(0, 0, "自分の位置", hero)
        add(0, 1, "戦う相手の位置", villain)
        add(1, 0, "残る相手の人数", opponents)
        add(1, 1, "ポット(ベット込み)",
            tk.Entry(box, textvariable=self.var_pot, width=8,
                     font=self.f_base))
        add(2, 0, "払う額(0=ベット無し)",
            tk.Entry(box, textvariable=self.var_call, width=8,
                     font=self.f_base))
        add(2, 1, "残りスタック",
            tk.Entry(box, textvariable=self.var_stack, width=8,
                     font=self.f_base))
        add(3, 0, "相手の前の行動",
            ttk.Combobox(box, textvariable=self.var_villain_action, width=12,
                         state="readonly",
                         values=list(st.VILLAIN_ACTION_KEEP)))

        opt = tk.Frame(box, bg=BG)
        opt.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        tk.Checkbutton(opt, text="相手のレンジを考慮(外すとランダム相手)",
                       variable=self.var_use_range, bg=BG, font=self.f_base,
                       command=self._request_calc).pack(side="left")
        tk.Label(opt, text="試行回数", bg=BG,
                 font=self.f_base).pack(side="left", padx=(14, 4))
        ttk.Combobox(opt, textvariable=self.var_iters, width=8,
                     state="readonly",
                     values=["5000", "20000", "60000"]).pack(side="left")

        for var in (self.var_hero, self.var_villain, self.var_opponents,
                    self.var_pot, self.var_call, self.var_stack,
                    self.var_iters, self.var_villain_action):
            var.trace_add("write", lambda *_a: self._request_calc())

        act = tk.Frame(box, bg=BG)
        act.grid(row=5, column=0, columnspan=4, sticky="we", pady=(8, 0))
        tk.Button(act, text="計算する", font=self.f_bold,
                  command=self._request_calc).pack(side="left")
        self.progress = ttk.Progressbar(act, length=170, mode="determinate",
                                        maximum=100)
        self.progress.pack(side="left", padx=10)
        self.status = tk.Label(act, text="", bg=BG, font=self.f_small,
                               fg="#555")
        self.status.pack(side="left")

    def _build_tournament(self, parent):
        box = tk.LabelFrame(parent, text="トーナメント(ポーカーチェイス)", bg=BG,
                            font=self.f_bold, padx=8, pady=6)
        box.pack(fill="x", pady=(10, 0))

        self.var_tournament = tk.BooleanVar(value=True)
        self.var_points = tk.StringVar(value=icm.DEFAULT_PRESET)
        self.var_villain_stack = tk.StringVar(value="25")
        self.var_others = tk.StringVar(value="30,28,35,20")
        self.var_ante = tk.StringVar(value="0.1")

        tk.Checkbutton(box, text="順位ポイントで判断する(外すとチップの期待値)",
                       variable=self.var_tournament, bg=BG, font=self.f_base,
                       command=self._request_calc).grid(
            row=0, column=0, columnspan=4, sticky="w")

        def add(row, col, text, widget):
            tk.Label(box, text=text, bg=BG, font=self.f_base).grid(
                row=row, column=col * 2, sticky="e", padx=(0, 4), pady=3)
            widget.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 16))

        add(1, 0, "順位ポイント",
            ttk.Combobox(box, textvariable=self.var_points, width=22,
                         state="readonly",
                         values=list(icm.POINT_PRESETS)))
        add(2, 0, "相手のスタック",
            tk.Entry(box, textvariable=self.var_villain_stack, width=8,
                     font=self.f_base))
        add(2, 1, "他の人(カンマ区切り)",
            tk.Entry(box, textvariable=self.var_others, width=16,
                     font=self.f_base))
        ante_row = tk.Frame(box, bg=BG)
        ante_row.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        tk.Label(ante_row, text="アンティ(1人あたりBB)", bg=BG,
                 font=self.f_base).pack(side="left")
        tk.Entry(ante_row, textvariable=self.var_ante, width=6,
                 font=self.f_base).pack(side="left", padx=4)
        tk.Button(ante_row, text="プリフロップのポットを計算", font=self.f_base,
                  command=self._fill_preflop_pot).pack(side="left", padx=6)

        chart_row = tk.Frame(box, bg=BG)
        chart_row.grid(row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.chart_button = tk.Button(
            chart_row, text="このスタックのレンジ表を作る", font=self.f_bold,
            command=self._make_range_chart)
        self.chart_button.pack(side="left")
        self.chart_status = tk.Label(chart_row, text="", bg=BG,
                                     font=self.f_small, fg="#555")
        self.chart_status.pack(side="left", padx=8)

        tk.Label(box, text="スタックはすべて BB 単位。自分の分は上の「残りスタック」",
                 bg=BG, font=self.f_small, fg="#777").grid(
            row=4, column=0, columnspan=4, sticky="w")

        for var in (self.var_points, self.var_villain_stack, self.var_others):
            var.trace_add("write", lambda *_a: self._request_calc())

    def _fill_preflop_pot(self):
        """SB + BB + 全員のアンティ でプリフロップの開始ポットを入れる。

        ポーカーチェイスのランク戦はアンティありなので、
        これを入れ忘れるとポットオッズが実際より不利に出る。
        """
        try:
            ante = float(self.var_ante.get() or 0)
        except ValueError:
            self.status.configure(text="アンティは数字で入れてください")
            return
        players = 2 + len(
            [x for x in self.var_others.get().split(",") if x.strip()])
        pot = 1.5 + ante * players
        self.var_pot.set("%g" % round(pot, 2))
        self.var_call.set("0")
        self.status.configure(text="ポット %.2f BB(SB+BB+アンティ%d人分)を入れました"
                                   % (pot, players))

    # -----------------------------------------------------
    # スタックの深さに合わせたレンジ表
    # -----------------------------------------------------
    def _make_range_chart(self):
        if self.chart_busy:
            self.chart_gen += 1
            self.chart_busy = False
            self.chart_button.configure(text="このスタックのレンジ表を作る")
            self.chart_status.configure(text="中止しました")
            return

        sit, reason = self._read_situation()
        if sit is None:
            self.chart_status.configure(text=reason)
            return
        if sit.street != "プリフロップ":
            self.chart_status.configure(
                text="レンジ表はプリフロップ用。場のカードを消してください")
            return

        self.chart_gen += 1
        gen = self.chart_gen
        self.chart_busy = True
        self.chart_button.configure(text="中止")
        self.chart_status.configure(text="169ハンドを計算中... 0%")

        def should_stop():
            return gen != self.chart_gen

        def report(frac):
            self.queue.put(("chart_progress", gen, frac))

        def work():
            try:
                table = ranges_gen.generate(sit, progress=report,
                                            should_stop=should_stop)
                self.queue.put(("chart_done", gen, (sit, table)))
            except KeyboardInterrupt:
                self.queue.put(("chart_cancelled", gen, None))
            except Exception as exc:
                self.queue.put(("chart_error", gen, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _handle_chart_message(self, kind, gen, payload):
        if gen != self.chart_gen:
            return
        if kind == "chart_progress":
            self.chart_status.configure(text="169ハンドを計算中... %.0f%%"
                                             % (payload * 100))
            return
        self.chart_busy = False
        self.chart_button.configure(text="このスタックのレンジ表を作る")
        if kind == "chart_error":
            self.chart_status.configure(text="エラー: %s" % payload)
            return
        if kind == "chart_cancelled":
            self.chart_status.configure(text="中止しました")
            return
        sit, table = payload
        self.chart_status.configure(text="できました")
        self._show_range_chart(sit, table)

    def _show_range_chart(self, sit, table):
        """13x13 のレンジ表を別ウィンドウで出す。"""
        win = tk.Toplevel(self.root)
        win.title("%s / %.0fBB のレンジ表" % (sit.hero_pos, sit.stack))
        win.configure(bg=BG)

        head = tk.Label(
            win, bg=BG, font=self.f_bold, justify="left", anchor="w",
            text="%s・%.0fBB・順位ポイント基準で計算したオープンレンジ"
                 % (sit.hero_pos, sit.stack))
        head.pack(anchor="w", padx=10, pady=(10, 2))

        legend = tk.Frame(win, bg=BG)
        legend.pack(anchor="w", padx=10)
        for name in (ranges_gen.ALLIN, ranges_gen.RAISE, ranges_gen.FOLD):
            tk.Label(legend, text="  ", bg=ranges_gen.ACTION_COLORS[name],
                     relief="solid", bd=1).pack(side="left")
            tk.Label(legend, text=name + "  ", bg=BG,
                     font=self.f_small).pack(side="left")

        grid = tk.Frame(win, bg=BG)
        grid.pack(padx=10, pady=8)
        mine = pf.hand_class(sit.hole)
        for r, row in enumerate(ranges_gen.all_classes()):
            for c, cls in enumerate(row):
                action, value = table[cls]
                lbl = tk.Label(grid, text=cls, width=4, font=self.f_small,
                               bg=ranges_gen.ACTION_COLORS[action],
                               relief="ridge", bd=1)
                if cls == mine:
                    lbl.configure(font=self.f_bold, bd=3, relief="solid")
                lbl.grid(row=r, column=c, padx=0, pady=0)

        d = ranges_gen.compare_with_cash(table, sit.hero_pos)
        text = tk.Text(win, height=7, width=76, font=self.f_mono,
                       wrap="word", bg=PANEL_BG, relief="flat")
        text.pack(fill="x", padx=10, pady=(0, 10))
        text.insert("end",
                    "参加する割合: この表 %.0f%% / キャッシュ用チャート %.0f%%\n"
                    % (d["生成した表の割合"], d["キャッシュ表の割合"]))
        text.insert("end", "この表だけ参加: %s\n"
                    % (", ".join(d["生成側だけ参加"]) or "なし"))
        text.insert("end", "キャッシュ表だけ参加: %s\n"
                    % (", ".join(d["キャッシュ側だけ参加"]) or "なし"))
        text.insert("end", "\nこのスタック構成・順位ポイント・アンティで"
                           "計算した結果。スタックが変われば表も変わる。")
        text.configure(state="disabled")

    def _build_results(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", pady=(10, 0))

        self.equity_label = tk.Label(top, text="勝率 --", bg=BG,
                                     font=self.f_big, anchor="w",
                                     justify="left", wraplength=420)
        self.equity_label.pack(anchor="w")
        self.bar = tk.Canvas(top, height=22, bg=PANEL_BG,
                             highlightthickness=1,
                             highlightbackground="#ccc")
        self.bar.pack(fill="x", pady=(2, 2))
        self.bar_legend = tk.Label(top, text="", bg=BG, font=self.f_small,
                                   fg="#555", anchor="w", justify="left",
                                   wraplength=420)
        self.bar_legend.pack(anchor="w")

        rank_box = tk.LabelFrame(parent, text="アクションの優先度(期待値の高い順)",
                                 bg=BG, font=self.f_bold, padx=6, pady=4)
        rank_box.pack(fill="x", pady=(8, 0))

        cols = ("action", "ev", "icm", "note")
        self.tree = ttk.Treeview(rank_box, columns=cols, show="headings",
                                 height=5, selectmode="browse")
        self.tree.heading("action", text="アクション")
        self.tree.heading("ev", text="チップEV(BB)")
        self.tree.heading("icm", text="順位EV(pt)")
        self.tree.heading("note", text="内訳")
        self.tree.column("action", width=160, anchor="w")
        self.tree.column("ev", width=90, anchor="e")
        self.tree.column("icm", width=80, anchor="e")
        self.tree.column("note", width=240, anchor="w")
        self.tree.pack(fill="x")
        self.tree.tag_configure("best", background="#e8f5e9")

        self._build_solver_panel(parent)

        detail_box = tk.LabelFrame(parent, text="内訳と注意", bg=BG,
                                   font=self.f_bold, padx=6, pady=4)
        detail_box.pack(fill="both", expand=True, pady=(8, 0))
        self.text = tk.Text(detail_box, height=16, width=46,
                            font=self.f_mono, wrap="word", bg=PANEL_BG,
                            relief="flat")
        scroll = ttk.Scrollbar(detail_box, orient="vertical",
                               command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("h", font=self.f_bold, foreground="#1a3a6b")
        self.text.tag_configure("warn", foreground="#a33")

    # -----------------------------------------------------
    # GTOソルバー(TexasSolver)
    # -----------------------------------------------------
    def _build_solver_panel(self, parent):
        box = tk.LabelFrame(parent, text="GTOソルバー(TexasSolver)", bg=BG,
                            font=self.f_bold, padx=6, pady=4)
        box.pack(fill="x", pady=(8, 0))

        row = tk.Frame(box, bg=BG)
        row.pack(fill="x")
        self.solver_button = tk.Button(row, text="ソルバーに聞く",
                                       font=self.f_bold,
                                       command=self._ask_solver)
        self.solver_button.pack(side="left")
        self.solver_status = tk.Label(row, text="", bg=BG, font=self.f_small,
                                      fg="#555", anchor="w", justify="left",
                                      wraplength=300)
        self.solver_status.pack(side="left", padx=8)

        self.solver_text = tk.Label(box, text="", bg=BG, font=self.f_mono,
                                    anchor="w", justify="left")
        self.solver_text.pack(fill="x", pady=(4, 0))

        cache_row = tk.Frame(box, bg=BG)
        cache_row.pack(fill="x", pady=(4, 0))
        self.cache_label = tk.Label(cache_row, text="", bg=BG,
                                    font=self.f_small, fg="#777")
        self.cache_label.pack(side="left")
        tk.Button(cache_row, text="消す", font=self.f_small,
                  command=self._clear_solver_cache).pack(side="left", padx=6)
        self._update_cache_label()

        if sv is None or sv.find_solver() is None:
            self.solver_button.configure(state="disabled")
            self.solver_status.configure(
                text="ソルバー未導入。solver_engine/README.md の手順で置くと使えます")

    def _update_cache_label(self):
        """解いた結果をいくつ貯めているか。同じ設定なら即答できる。"""
        if sv is None:
            return
        n, mb = sv.cache_info()
        self.cache_label.configure(
            text="解いた結果の保存: %d件 %.1fMB(同じ設定なら即答)" % (n, mb))

    def _clear_solver_cache(self):
        if sv is None:
            return
        n = sv.clear_cache()
        self._update_cache_label()
        self.solver_status.configure(text="保存していた結果 %d件を消しました" % n)

    def _ask_solver(self):
        if self.solver_busy:
            self._cancel_solver()
            return
        sit, reason = self._read_situation()
        if sit is None:
            self.solver_status.configure(text=reason)
            return
        if sit.street == "プリフロップ":
            self.solver_status.configure(
                text="ソルバーはフロップ以降だけ。プリフロップはレンジ表を見てください")
            return

        ip_range, oop_range, hero_is_ip = st.solver_ranges(sit)
        facing_bet = sit.to_call > 0
        self.solver_gen += 1
        gen = self.solver_gen
        self.solver_busy = True
        self.solver_started = time.time()
        self.solver_button.configure(text="中止")
        self.solver_text.configure(text="")
        expect = sv.EXPECTED_SECONDS.get(sit.street, 60)
        self.solver_status.configure(text="解いています(目安 %d秒)..." % expect)
        self._solver_tick()

        def should_stop():
            return gen != self.solver_gen

        def work():
            try:
                r = sv.solve_spot(
                    sit.hole, sit.board, ip_range, oop_range,
                    sit.pot, sit.stack, hero_is_ip, facing_bet,
                    should_stop=should_stop)
                self.queue.put(("solver_done", gen, r))
            except Exception as exc:
                self.queue.put(("solver_error", gen, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _cancel_solver(self):
        """走っているソルバーを打ち切る。世代番号を上げると子プロセスが落ちる。"""
        self.solver_gen += 1
        self.solver_busy = False
        self.solver_button.configure(text="ソルバーに聞く", state="normal")
        self.solver_status.configure(text="中止しました")

    def _solver_tick(self):
        """計算中の経過秒を出す。止まっていないことが分かるように。"""
        if not self.solver_busy:
            return
        elapsed = time.time() - self.solver_started
        base = self.solver_status.cget("text").split(" 経過")[0]
        self.solver_status.configure(text="%s 経過 %d秒" % (base, elapsed))
        self.root.after(1000, self._solver_tick)

    def _handle_solver_message(self, kind, gen, payload):
        if gen != self.solver_gen:
            return
        self.solver_busy = False
        self.solver_button.configure(text="ソルバーに聞く", state="normal")
        if kind == "solver_error":
            self.solver_status.configure(text="ソルバー: %s" % payload)
            return

        r = payload
        conv = ""
        if r.exploitability is not None:
            conv = " / 残り搾取率 %.2f%%" % r.exploitability
            if r.exploitability > 2.0:
                conv += "(まだ収束しきっていない)"
        if r.cached:
            head = "前に解いた結果を再利用(即答)"
        else:
            head = "%.0f秒で完了" % r.seconds
        self.solver_status.configure(
            text="%s%s   局面: %s" % (head, conv, r.node))
        self.solver_text.configure(text="\n".join(r.as_lines()))
        self._update_cache_label()

    # -----------------------------------------------------
    # カード入力
    # -----------------------------------------------------
    def _place_card(self, card):
        if card in self.slots:
            return
        i = self.focus_index
        if i >= TOTAL_SLOTS:
            return
        self.slots[i] = card
        self.focus_index = self._next_empty(i + 1)
        self._refresh_inputs()

    def _next_empty(self, start):
        for i in range(start, TOTAL_SLOTS):
            if self.slots[i] is None:
                return i
        for i in range(0, TOTAL_SLOTS):
            if self.slots[i] is None:
                return i
        return TOTAL_SLOTS

    def _focus_slot(self, i):
        self.focus_index = i
        self._refresh_inputs()

    def _clear_slot(self, i):
        self.slots[i] = None
        self.focus_index = i
        self._refresh_inputs()

    def _undo(self):
        for i in range(TOTAL_SLOTS - 1, -1, -1):
            if self.slots[i] is not None:
                self.slots[i] = None
                self.focus_index = i
                break
        self._refresh_inputs()

    def _clear_all(self):
        self.slots = [None] * TOTAL_SLOTS
        self.focus_index = 0
        self._refresh_inputs()

    def _refresh_inputs(self):
        for i, lbl in enumerate(self.slot_widgets):
            card = self.slots[i]
            if card is None:
                lbl.configure(text="", bg=SLOT_FOCUS
                              if i == self.focus_index else SLOT_EMPTY)
            else:
                lbl.configure(text=card_label(card), fg=card_color(card),
                              bg=SLOT_FOCUS if i == self.focus_index
                              else "white")
        used = set(c for c in self.slots if c is not None)
        for card, b in self.card_buttons.items():
            if card in used:
                b.configure(state="disabled", disabledforeground=USED_FG)
            else:
                b.configure(state="normal")
        self._request_calc()

    # -----------------------------------------------------
    # 計算
    # -----------------------------------------------------
    def _read_situation(self):
        """入力からSituationを作る。まだ計算できないときは (None, 理由)。"""
        hole = [c for c in self.slots[:HOLE_SLOTS] if c is not None]
        board = [c for c in self.slots[HOLE_SLOTS:] if c is not None]
        if len(hole) < 2:
            return None, "自分の手札を2枚選んでください"

        filled = [self.slots[i] is not None
                  for i in range(HOLE_SLOTS, TOTAL_SLOTS)]
        if filled[3] and not all(filled[:3]):
            return None, "フロップの3枚を先に入れてください"
        if filled[4] and not filled[3]:
            return None, "ターンを先に入れてください"
        if len(board) in (1, 2):
            return None, "フロップは3枚そろえてください"

        try:
            pot = float(self.var_pot.get() or 0)
            to_call = float(self.var_call.get() or 0)
            stack = float(self.var_stack.get() or 0)
        except ValueError:
            return None, "ポット・支払い額・スタックは数字で入れてください"
        if pot <= 0:
            return None, "ポットは0より大きい数字にしてください"
        if stack <= 0:
            return None, "スタックは0より大きい数字にしてください"

        hero = self.var_hero.get()
        villain = self.var_villain.get()
        if villain == hero:
            villain = "CO" if hero != "CO" else "BTN"

        try:
            villain_stack = float(self.var_villain_stack.get() or stack)
            others = [float(x) for x in self.var_others.get().split(",")
                      if x.strip()]
        except ValueError:
            return None, "スタックは数字をカンマ区切りで入れてください"

        sit = st.Situation(
            hole, board, hero_pos=hero, villain_pos=villain,
            opponents=int(self.var_opponents.get()),
            pot=pot, to_call=to_call, stack=stack,
            iters=int(self.var_iters.get()),
            use_range=self.var_use_range.get(),
            tournament=self.var_tournament.get(),
            points=icm.POINT_PRESETS.get(self.var_points.get()),
            villain_stack=villain_stack, other_stacks=others,
            villain_action=self.var_villain_action.get())
        return sit, ""

    def _request_calc(self):
        sit, reason = self._read_situation()
        if sit is None:
            self.generation += 1          # 走っている計算は捨てる
            self.status.configure(text=reason)
            self.progress["value"] = 0
            return
        if self.busy:
            self.pending = True           # 今の計算が終わったらやり直す
            self.generation += 1
            return
        self._start(sit)

    def _start(self, sit):
        self.generation += 1
        gen = self.generation
        self.busy = True
        self.pending = False
        self.status.configure(text="計算中...")
        self.progress["value"] = 0

        def should_stop():
            return gen != self.generation

        def report(frac):
            self.queue.put(("progress", gen, frac))

        def work():
            try:
                a = st.analyze(sit, progress=report, should_stop=should_stop)
                self.queue.put(("done", gen, (sit, a)))
            except Cancelled:
                self.queue.put(("cancelled", gen, None))
            except Exception as exc:                      # 想定外は画面に出す
                self.queue.put(("error", gen, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, gen, payload = self.queue.get_nowait()
                if kind == "progress":
                    if gen == self.generation:
                        self.progress["value"] = payload * 100
                    continue
                if kind.startswith("solver_"):
                    self._handle_solver_message(kind, gen, payload)
                    continue
                if kind.startswith("chart_"):
                    self._handle_chart_message(kind, gen, payload)
                    continue
                # 以下は計算の終了
                self.busy = False
                if kind == "done" and gen == self.generation:
                    self.progress["value"] = 100
                    self.status.configure(text="")
                    self._show(*payload)
                elif kind == "error":
                    self.status.configure(text="エラー: %s" % payload)
                if self.pending:
                    sit, reason = self._read_situation()
                    if sit is not None:
                        self._start(sit)
                    else:
                        self.pending = False
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    # -----------------------------------------------------
    # 結果表示
    # -----------------------------------------------------
    def _show(self, sit, a):
        eq = a.equity
        self.equity_label.configure(
            text="%s / 勝率 %.1f%%" % (a.street, eq.equity * 100))
        self._draw_bar(eq, a.pot_odds)

        legend = ("勝ち %.1f%% / 引き分け %.1f%% / 負け %.1f%%"
                  % (eq.win * 100, eq.tie * 100, eq.lose * 100))
        if a.pot_odds is not None:
            legend += "   縦線=必要勝率 %.1f%%" % (a.pot_odds * 100)
        self.bar_legend.configure(text=legend)

        for item in self.tree.get_children():
            self.tree.delete(item)
        for i, o in enumerate(a.options):
            head = o.detail[0] if o.detail else ""
            icm_text = "%+.2f" % o.icm_ev if o.icm_ev is not None else "-"
            self.tree.insert("", "end",
                             values=(("★ " if i == 0 else "   ") + o.name,
                                     "%+.2f" % o.ev, icm_text, head),
                             tags=("best",) if i == 0 else ())

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._write("推奨: %s\n" % (a.best.name if a.best else "-"), "h")
        if a.finish:
            self._write("今の順位確率: %s\n" % a.finish)
        if a.icm_pot_odds is not None:
            self._write("必要勝率 ICM込み %.1f%% / チップ基準 %.1f%%\n"
                        % (a.icm_pot_odds * 100, (a.pot_odds or 0) * 100))
        if a.chart:
            hand, note = a.chart
            self._write("レンジ表の推奨: %s\n  %s\n" % (hand, note))
            self._write("  プリフロップは期待値計算よりレンジ表を優先して良い\n")
        if a.pushfold:
            self._write("\nオールインするか降りるか\n", "h")
            for line in a.pushfold.as_lines():
                self._write("  %s\n" % line)
        self._write("\n")

        self._write("手の状況\n", "h")
        for line in draws.summary_lines(a.draw):
            self._write("  %s\n" % line)
        if a.spr is not None:
            self._write("  SPR(スタック/ポット) %.1f\n" % a.spr)
        self._write("\n")

        self._write("各アクションの内訳\n", "h")
        for o in a.options:
            self._write("  %s  期待値 %+.2f BB\n" % (o.name, o.ev))
            for d in o.detail:
                self._write("      - %s\n" % d)
        self._write("\n")

        self._write("注意\n", "h")
        for n in a.notes:
            self._write("  ・%s\n" % n, "warn")
        self.text.configure(state="disabled")

    def _write(self, text, tag=None):
        self.text.insert("end", text, (tag,) if tag else ())

    def _draw_bar(self, eq, pot_odds):
        self.bar.delete("all")
        self.bar.update_idletasks()
        w = max(self.bar.winfo_width(), 200)
        h = int(self.bar["height"])
        x = 0
        for frac, color in ((eq.win, WIN_COLOR), (eq.tie, TIE_COLOR),
                            (eq.lose, LOSE_COLOR)):
            seg = w * frac
            self.bar.create_rectangle(x, 0, x + seg, h, fill=color,
                                      outline="")
            x += seg
        if pot_odds is not None:
            px = w * pot_odds
            self.bar.create_line(px, 0, px, h, fill="black", width=2)


def fit_to_content(root, extra_height=140):
    """中身が入りきる大きさにウィンドウを合わせる(画面からはみ出さない範囲で)。

    画面の拡大率(tk scaling)が環境によって違うので、
    決め打ちのサイズだと右側が切れることがある。中身の要求サイズから決める。
    """
    root.update_idletasks()
    w = root.winfo_reqwidth() + 24
    h = root.winfo_reqheight() + extra_height
    w = min(w, root.winfo_screenwidth() - 40)
    h = min(h, root.winfo_screenheight() - 80)
    root.geometry("%dx%d" % (w, h))
    root.minsize(min(w, root.winfo_screenwidth() - 40), 520)


def main():
    root = tk.Tk()
    App(root)
    fit_to_content(root)
    root.mainloop()


if __name__ == "__main__":
    main()
