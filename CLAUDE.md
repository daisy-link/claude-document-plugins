# このリポジトリの作業ルール

Claude Code プラグインのマーケットプレイス（`lo-cal-skills`）です。

## バージョン番号は絶対に下げない

`plugins/spec-tools/.claude-plugin/plugin.json` の `version` は、**履歴上の最大値より必ず大きく**する。

Claude Code はインストール済みバージョンと配布側バージョンを比較し、**配布側が小さいか同じなら
「既に最新」と判定して更新しない。** 一度でも番号を下げると、その後いくら上げても
過去の最大値を超えるまで**利用者側で `/plugin update` が効かなくなる**。

- 直前のコミットの番号だけを見て `+1` しない（その番号自体が巻き戻っている可能性がある）
- **バンプ前に必ず履歴上の最大値を確認する**：

  ```bash
  git log --format="%H" -- plugins/spec-tools/.claude-plugin/plugin.json | while read h; do
    git show "$h:plugins/spec-tools/.claude-plugin/plugin.json" 2>/dev/null \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['version'])" 2>/dev/null
  done | sort -t. -k1,1n -k2,2n -k3,3n | tail -1
  ```

- 上げ幅の目安：スキルの追加・仕様変更は minor（`0.30.0` → `0.31.0`）、不具合修正・文言調整は
  patch（`0.30.0` → `0.30.1`）
- 過去に実際に `0.29.0` → `0.24.2` へ巻き戻り、利用者がプラグインを更新できなくなる事故が起きている

## スキルを追加・変更したら揃えて直す

| 対象 | 内容 |
|------|------|
| `plugins/spec-tools/.claude-plugin/plugin.json` | `version` と `description` |
| `plugins/spec-tools/skills/共通ルール.md` | 冒頭のスキル一覧 |
| `README.md` | ユースケース表・スキル一覧表 |

## git の扱い

- **push はユーザーが行う。** Claude はコミットまで
- コミットはユーザーから明示的に依頼されたときだけ
- `git add -A` / `git add .` を使わず、**変更したファイルを個別に指定**する

## 変更を試すには新しいセッションが必要

スキルの内容はセッション開始時に読み込まれるため、**実行中のセッションで
`/plugin update` しても反映されない。** 動作確認は新しいセッションで行う。
