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

# .envファイルにAPI keyを設定
echo "ANTHROPIC_API_KEY=your-key-here" >> .env
echo "OBSIDIAN_VAULT_PATH=/Users/username/Documents/Obsidian Vault" >> .env
```

## 使い方

### 基本的な流れ

1. PDFを `~/Documents/Obsidian Vault/MyPage/Research/downloads/` に配置
2. `python process_papers.py` 実行
3. 論文タイプ別のディレクトリに自動分類・要約

### コマンド形式

```bash
python process_papers.py [論文タイプ] [言語]
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

### 言語指定

```bash
# 日本語で要約
python process_papers.py ja

# 英語で要約
python process_papers.py en

# 論文の言語に合わせて要約（デフォルト）
python process_papers.py
```

### 論文タイプと言語を両方指定

```bash
# 実証論文を日本語で要約
python process_papers.py empirical ja

# 理論論文を英語で要約
python process_papers.py theoretical en

# レビュー論文を日本語で要約
python process_papers.py review ja

# 順序は自由（タイプと言語を判別）
python process_papers.py ja empirical
```

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
  🤖 Claude API呼び出し中 ⠹
  ✅ Claude API呼び出し完了
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

- Claude Sonnet 4.5使用
- 1論文あたり約10円
- 月30本処理で約300円

## トラブルシューティング

### エラー: ANTHROPIC_API_KEYが設定されていません

`.env`ファイルにAPI keyを設定してください：

```bash
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
```

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
