# Paper Sage 📚

経営学論文の自動要約システム

## 機能

- PDF論文を自動でテキスト抽出
- Claude Sonnet 4で高品質な要約生成
- 論文タイプ別の最適化されたプロンプト（実証/理論/レビュー）
- 要約言語の指定（日本語/英語）
- Obsidian vaultに自動保存

## セットアップ

```bash
# 仮想環境を有効化
source venv/bin/activate

# 依存関係をインストール
pip install -r requirements.txt

# Claude Code CLI をインストールし、サブスクでログイン（APIキーは不要）
claude login

# .envファイルを設定
echo "OBSIDIAN_VAULT_PATH=/Users/username/Documents/Obsidian Vault" >> .env
```

## 使い方

### 基本的な流れ

1. PDFを `~/Documents/Obsidian Vault/MyPage/Research/downloads/` に配置
2. `python process_papers.py` 実行
3. 論文タイプ別のディレクトリに自動分類・要約

### コマンド形式

```bash
python process_papers.py [論文タイプ] [ファイル名.pdf]
```

### 論文タイプ指定

```bash
# 自動判定（論文の内容から判断）
python process_papers.py

# 実証論文として処理
python process_papers.py empirical

# 理論論文として処理
python process_papers.py theoretical

# レビュー論文として処理
python process_papers.py review
```

### 1本だけ指定して処理

```bash
# downloads内の特定のPDFだけを処理
python process_papers.py paper.pdf

# 論文タイプも指定
python process_papers.py empirical paper.pdf
```

### 要約言語

論文本文から自動判定されます（日本語の論文は日本語、それ以外は英語）。

### ヘルプの表示

```bash
python process_papers.py --help
# または
python process_papers.py -h
```

## 実行例

```bash
$ python process_papers.py empirical ja

✅ プロンプトファイル読み込み完了

📌 論文タイプ指定: empirical
🌐 要約言語: 日本語

============================================================
📚 2本のPDFを処理します
============================================================

[1/2]
============================================================
📄 処理中: strategic_management_2024.pdf
============================================================
  ✅ テキスト抽出完了: 45,234 文字
  📋 指定タイプ: empirical
  📁 ディレクトリ作成: MyPage/Research/empirical/strategic_management_2024
  📦 PDF移動完了
  🤖 Claude呼び出し中 ⠹
  ✅ Claude呼び出し完了
  ✅ 保存完了: summary.md

============================================================
🎉 処理完了！
   成功: 2/2本
============================================================
```

## ディレクトリ構造

```
MyPage/Research/
├── downloads/              # PDFを投げ込む場所（処理後は空になる）
├── empirical/              # 実証論文
│   └── paper_name/
│       ├── paper.pdf
│       └── summary.md
├── theoretical/            # 理論論文
│   └── paper_name/
│       ├── paper.pdf
│       └── summary.md
└── review/                 # レビュー論文
    └── paper_name/
        ├── paper.pdf
        └── summary.md
```

## コスト

- Claudeサブスクの利用枠を消費（API課金なし）

## トラブルシューティング

### エラー: claude コマンドが見つかりません

Claude Code をインストールし、`claude login` でサブスクにログインしてください。
`.env` に `ANTHROPIC_API_KEY` が残っているとAPI課金になるため、削除してください。

### エラー: プロンプトファイルが見つかりません

`~/Documents/Obsidian Vault/_prompts/`ディレクトリに以下のファイルがあることを確認：

- `system.md`
- `empirical.md`
- `theoretical.md`
- `review.md`

### 処理するPDFがありません

`~/Documents/Obsidian Vault/MyPage/Research/downloads/`にPDFファイルを配置してください。

## ライセンス

MIT
