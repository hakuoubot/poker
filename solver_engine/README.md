# solver_engine — GTOソルバー(TexasSolver)の置き場

麻雀版の `akochan_engine/` と同じ位置づけ。**ソルバー本体はこのリポジトリに入れない。**
ライセンスが別（TexasSolver は AGPL-3.0）なので混ぜないでおく。

## 置き方

1. https://github.com/bupticybee/TexasSolver/releases から
   `TexasSolver-v0.2.0-Windows.zip`（41.4MB）を落とす
2. このフォルダに展開する。こうなっていれば良い

```
solver_engine/
  solver_bridge.py                       ← このリポジトリのコード
  TexasSolver-v0.2.0-Windows/
    console_solver.exe                   ← これを探して呼ぶ
    resources/ ranges/ parameters/ ...
```

3. 動作確認

```
python solver_engine/solver_bridge.py
```

`find_solver()` がフォルダ名にかかわらず `console_solver.exe` を探すので、
新しいバージョンに差し替えても動く。置いていなければ GUI のソルバー欄が
グレーアウトするだけで、アプリ自体は普通に動く。

## 実測(14スレッド / 16GB)

| ストリート | 時間 | 残り搾取率 |
| --- | --- | --- |
| フロップ | 約82秒 | 0.67% |
| ターン | 約7秒 | 1.3% |
| リバー | 数秒 | — |

フロップが重いのはソルバー側の相場（公式ベンチも6スレッドで172秒）。

**反復回数を減らして速くするのは駄目。** 実測で、25反復まで削ると
同じ局面の答えが「ベット61% / チェック39%」に変わってしまった
（十分に回すと「ベット96〜98%」）。時間を削るなら反復ではなく木の枝を削る。
`build_commands()` はベットサイズを1種類にし、レイズを今のストリートだけに
限定している（全ストリートにレイズを入れると 68秒 → 269秒 になる）。

## 制限

- **ポストフロップ専用。** プリフロップは `preflop.py` のチャートのまま。
- **2人用。** 3人以上の局面は解けない（TexasSolver の仕様）。
- レンジは今のところポジションから自動で決めている。
  相手の癖に合わせるには169マスのレンジ入力UIが要る（未実装）。
