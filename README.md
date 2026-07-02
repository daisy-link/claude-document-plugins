# lo-cal Claude Code スキル・マーケットプレイス

lo-cal 社内向けの Claude Code プラグインを配布するリポジトリです。
このリポジトリ自体が Claude Code のマーケットプレイス（`lo-cal-skills`）になっており、
プラグインをインストールすると付属のスキルがそのまま使えます。

## 収録プラグイン

### `spec-tools`

ソースコードから仕様書を生成し、閲覧用HTMLに変換する2つのスキルを同梱。

| スキル | 用途 |
|-------|------|
| system-spec-writer | リポジトリを分析してMarkdown形式の仕様書（要件定義・基本設計・詳細設計・システム設定・インフラ・開発環境）を作成・更新・再作成する |
| spec-to-html | 生成した仕様書を、ネット接続なしで開ける完全オフラインHTMLに変換する（Mermaid図・GitHub Alerts対応） |

## インストール

Claude Code で以下を実行します。

```
/plugin marketplace add <このリポジトリのGit URL>
/plugin install spec-tools@lo-cal-skills
```

> ローカルで試す場合は Git URL の代わりにこのリポジトリのパスを指定できます。
> `/plugin marketplace add /path/to/claude-skills`

インストール後は Claude に依頼するだけで、該当スキルが自動的に使われます。

## 使い方

### 仕様書を作成する
対象プロジェクトのディレクトリで Claude Code を起動し、「仕様書を作って」と依頼する。
生成物はプロジェクト直下の `docs/` に書き込まれます。

### 仕様書をHTMLに変換する
「仕様書をHTMLにして」と依頼する。`docs/` を読み取り、`docs-site/` にオフラインHTMLを出力します。
`docs-site/index.html` をブラウザで開くだけで、ネット接続なしに全仕様書を閲覧できます。

## リポジトリ構成

```
claude-skills/                          # = マーケットプレイス lo-cal-skills
├── .claude-plugin/
│   └── marketplace.json                # 配布カタログ
└── plugins/
    └── spec-tools/                     # プラグイン
        ├── .claude-plugin/plugin.json  # プラグイン定義
        └── skills/
            ├── system-spec-writer/     # SKILL.md + references/
            └── spec-to-html/           # SKILL.md + scripts/ + assets/
```

## 開発・メンテナンス

- スキルの追加・修正は `plugins/spec-tools/skills/` 配下で行う。
- 新しいプラグインを追加したら `.claude-plugin/marketplace.json` の `plugins` 配列に登録する。
- 変更を配布する際は `plugin.json` の `version` を更新する。利用者は `/plugin` から更新できる。
