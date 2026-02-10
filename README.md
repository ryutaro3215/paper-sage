# Paper Sage 📚

経営学論文の自動要約システム

## セットアップ

```bash
source venv/bin/activate
pip install -r requirements.txt
```

## 使い方

1. PDFを `~/Documents/Obsidian Vault/MyPage/Research/downloads/` に配置
2. `python process_papers.py` 実行
3. 論文タイプ別のディレクトリに自動分類・要約

## 論文タイプ指定

```bash
# 自動判定
python process_papers.py

# 手動指定
python process_papers.py empirical
python process_papers.py theoretical
python process_papers.py review
```
