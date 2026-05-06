# Backgammon (Pygame Zero, vs AI)

Pygame Zeroで動く、AI対戦バックギャモンです。

## 実行方法

```bash
uv run --with pgzero pgzrun src/backgammon/pgz_backgammon.py
```

## 遊び方

- `ダイスをクリック`: サイコロを振る（自分ターン）
- マウスクリック: 駒を選択して移動
- ダブリングキューブをドラッグ: ダブル提案（自分ターン、未ロール時、キューブ所有権がある時）
- `TAKE` / `DROP` ボタン: AI のダブル提案を受諾 / 拒否
- `REPLAY` ボタン: 終局後にリスタート
