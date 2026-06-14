#!/usr/bin/env python3
"""
Paper Sage - 論文自動要約システム
経営学論文をタイプ別に分類し、Claude APIで要約を生成
"""

from pathlib import Path
import anthropic
import PyPDF2
from datetime import datetime
import os
from dotenv import load_dotenv
import sys
import threading
import time
import shutil
import re

class PaperProcessor:
    def __init__(self, api_key, vault_path):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.vault_path = Path(vault_path)
        self.research_dir = self.vault_path / "MyPage/Research"
        self.downloads_dir = self.research_dir / "_inbox/downloads"
        self.prompts_dir = self.vault_path / "MyPage/Research/_prompts"

        # 論文タイプ別のディレクトリ
        self.paper_dirs = {
            "empirical": self.research_dir / "papers/empirical",
            "theoretical": self.research_dir / "papers/theoretical",
            "review": self.research_dir / "papers/review"
        }
        
        self.load_prompts()
    
    def load_prompts(self):
        """プロンプトファイルを読み込み"""
        try:
            self.system_prompt = (self.prompts_dir / "system.md").read_text(encoding='utf-8')
            self.prompts = {
                "empirical": (self.prompts_dir / "empirical.md").read_text(encoding='utf-8'),
                "theoretical": (self.prompts_dir / "theoretical.md").read_text(encoding='utf-8'),
                "review": (self.prompts_dir / "review.md").read_text(encoding='utf-8')
            }
            print("✅ プロンプトファイル読み込み完了")
        except FileNotFoundError as e:
            print(f"❌ エラー: プロンプトファイルが見つかりません: {e}")
            sys.exit(1)
    
    def extract_text(self, pdf_path):
        """PDFからテキスト抽出"""
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text()
        return text
    
    def detect_paper_type(self, text):
        """Claude APIで論文タイプを判定"""
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": f"""以下の論文（冒頭部分）のタイプを判定してください。
必ず empirical / theoretical / review のいずれか1語のみ答えてください。

- empirical: 仮説検証・データ収集・統計分析を行う実証研究
- theoretical: 理論構築・概念フレームワーク提案が中心
- review: 既存文献のレビュー・メタ分析が中心

論文冒頭:
{text[:3000]}

回答（1語のみ）:"""
            }]
        )
        result = response.content[0].text.strip().lower()
        if result not in ["empirical", "theoretical", "review"]:
            print(f"  ❌ 予期しない分類結果: '{result}'")
            return None
        return result
    
    def summarize(self, text, paper_type, language=None):
        """Claude APIで要約生成（ローディングアニメーション付き）"""
        # 対応するプロンプトを取得
        task_prompt = self.prompts.get(paper_type, self.prompts["empirical"])
        
        # 言語指定の追加プロンプト
        language_instruction = ""
        if language == 'ja':
            language_instruction = "\n**重要: 必ず日本語で要約を出力してください。**\n"
        elif language == 'en':
            language_instruction = "\n**Important: You must output the summary in English.**\n"
        
        # システムプロンプトとタスクプロンプトを結合
        full_prompt = f"""{self.system_prompt}
{language_instruction}
---

{task_prompt}

---

論文テキスト:
{text[:150000]}
"""
        
        print(f"  🤖 Claude API呼び出し中", end="", flush=True)
        
        # ローディングアニメーション
        stop_loading = threading.Event()
        
        def loading_animation():
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            while not stop_loading.is_set():
                print(f"\r  🤖 Claude API呼び出し中 {frames[idx % len(frames)]}", end="", flush=True)
                idx += 1
                time.sleep(0.1)
        
        loading_thread = threading.Thread(target=loading_animation, daemon=True)
        loading_thread.start()
        
        try:
            message = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=5000,
                messages=[{
                    "role": "user",
                    "content": full_prompt
                }]
            )
            result = message.content[0].text
        finally:
            stop_loading.set()
            loading_thread.join()
            print(f"\r  ✅ Claude API呼び出し完了                    ")
        
        return result
    
    def verify_hypotheses(self, text, summary):
        """要約中の仮説記述を原文と照合して検証・修正"""
        prompt = f"""あなたは経営学論文の査読者です。以下の【論文原文】と【要約】を照合し、要約のHypothesisセクションに仮説の読み違いや誤りがないかを厳密に確認してください。

確認の手順:
1. 【論文原文】から全ての仮説（H1, H2... またはHypothesis 1, 2...）を抽出する
2. 【要約】のHypothesisセクションに記載された各仮説と、原文の仮説を1対1で照合する
3. 以下の点を確認する:
   - 仮説の方向性（正の関係・負の関係・調整効果の向き）が正確か
   - 仮説で扱う変数・概念が正確に記述されているか
   - 仮説の番号・対応関係が正しいか
   - 仮説が欠落していないか

修正が必要な場合は、【要約】のHypothesisセクション全体を正確な内容に書き直して出力してください。
修正が不要な場合は「VERIFIED: 仮説の記述に問題はありません。」とだけ出力してください。

【論文原文】（仮説が記載されている箇所を中心に抽出）:
{text[:80000]}

【要約】:
{summary}
"""
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()

    def generate_keywords(self, text, language=None):
        """論文の重要キーワードとその日本語訳を生成"""
        lang_instruction = ""
        if language == 'en':
            lang_instruction = "Output the keyword table in English (keep Japanese column as Japanese)."

        prompt = f"""以下の論文から重要なキーワードを5〜10個抽出し、必ず以下のMarkdownテーブル形式で出力してください。

| キーワード | 日本語訳 | 説明（1文） |
|-----------|----------|-------------|
| (英語キーワード) | (日本語訳) | (その論文文脈での意味・役割を1文で) |

{lang_instruction}
- 論文のコア概念・理論・手法に絞ること
- キーワードは論文中で実際に使われている用語を優先すること
- テーブル以外のテキストは一切出力しないこと

論文テキスト（冒頭部分）:
{text[:8000]}
"""
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()

    def extract_references(self, text):
        """論文の参考文献セクションを全文抽出し、箇条書きに整形"""
        pattern = re.compile(r'(?m)^[ \t]*(REFERENCES|References|BIBLIOGRAPHY|Bibliography|WORKS CITED|Works Cited)[ \t]*$')
        matches = list(pattern.finditer(text))

        if not matches:
            return "（参考文献セクションが見つかりませんでした）"

        # 最後のマッチ（本文中の引用ではなくセクション見出し）を使用
        ref_start = matches[-1].start()
        refs_text = text[ref_start:].strip()
        print(f"\n  📍 参考文献セクション検出（テキスト位置: {ref_start:,}文字目）", end="", flush=True)

        return self._format_references_as_bullets(refs_text)

    def _format_references_as_bullets(self, refs_text):
        """参考文献テキストを箇条書きに整形"""
        lines = refs_text.splitlines()

        # ヘッダー行（REFERENCES等）を除去
        if lines:
            lines = lines[1:]

        # 参考文献の区切りパターン: 番号付き ([1], 1., 1 ) または著者名で始まる新エントリ
        ref_start_pattern = re.compile(
            r'^\s*(?:\[\d+\]|\d+[\.\)])\s+'  # [1] or 1. or 1)
            r'|^\s*[A-Z][a-z]+,\s'            # Author, (author-year style)
        )

        entries = []
        current_entry = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # 空行は区切りの可能性 - 現エントリを確定
                if current_entry:
                    entries.append(' '.join(current_entry))
                    current_entry = []
            elif ref_start_pattern.match(line):
                # 新しい参考文献エントリの始まり
                if current_entry:
                    entries.append(' '.join(current_entry))
                current_entry = [stripped]
            else:
                # 継続行
                current_entry.append(stripped)

        if current_entry:
            entries.append(' '.join(current_entry))

        if not entries:
            return refs_text  # 整形失敗時はそのまま返す

        return '\n'.join(f'- {entry}' for entry in entries if entry.strip())

    def process_paper(self, pdf_path, paper_type=None, language=None):
        """論文を処理"""
        print(f"\n{'='*60}")
        print(f"📄 処理中: {pdf_path.name}")
        print(f"{'='*60}")
        
        # テキスト抽出
        try:
            text = self.extract_text(pdf_path)
            print(f"  ✅ テキスト抽出完了: {len(text):,} 文字")
        except Exception as e:
            print(f"  ❌ PDF読み込みエラー: {e}")
            return
        
        # 論文タイプ判定（指定がない場合）
        if paper_type is None:
            paper_type = self.detect_paper_type(text)
            if paper_type is None:
                print(f"  ❌ 論文タイプを判定できませんでした。処理を中止します。")
                return
            print(f"  📋 判定結果: {paper_type}")
        else:
            print(f"  📋 指定タイプ: {paper_type}")
        
        # 論文用ディレクトリ作成
        target_dir = self.paper_dirs[paper_type]
        paper_dir = target_dir / pdf_path.stem
        paper_dir.mkdir(parents=True, exist_ok=True)
        print(f"  📁 ディレクトリ作成: {paper_dir.relative_to(self.vault_path)}")
        
        # PDFを移動
        new_pdf_path = paper_dir / pdf_path.name
        try:
            pdf_path.rename(new_pdf_path)
            print(f"  📦 PDF移動完了")
        except Exception as e:
            print(f"  ❌ PDF移動エラー: {e}")
            return
        
        # 要約生成
        try:
            summary = self.summarize(text, paper_type, language)
        except Exception as e:
            print(f"  ❌ 要約エラー: {e}")
            # PDFを元に戻す
            new_pdf_path.rename(pdf_path)
            return

        # 仮説の検証（empiricalのみ）
        if paper_type == "empirical":
            try:
                print(f"  🔍 仮説を原文と照合中...", end="", flush=True)
                verification = self.verify_hypotheses(text, summary)
                if verification.startswith("VERIFIED"):
                    print(f"\r  ✅ 仮説の検証完了: 問題なし                    ")
                else:
                    print(f"\r  ⚠️  仮説に修正が必要です。修正を適用します...                    ")
                    import re as _re
                    hypothesis_pattern = _re.compile(
                        r'(##\s*(?:\d+\.\s*)?(?:\*\*)?Hypothesis(?:\*\*)?.*?)(?=\n##\s*(?:\d+\.\s*)?\*?\*?[A-Z])',
                        _re.DOTALL
                    )
                    if hypothesis_pattern.search(summary):
                        summary = hypothesis_pattern.sub(verification + "\n\n", summary, count=1)
                    else:
                        summary = summary + f"\n\n---\n\n> **[仮説検証メモ]** 以下の修正が検出されました:\n>\n> " + verification.replace('\n', '\n> ')
                    print(f"  ✅ 仮説の修正を適用しました")
            except Exception as e:
                print(f"\n  ⚠️  仮説検証失敗 (スキップします): {e}")

        # キーワード生成
        try:
            print(f"  🔑 キーワード抽出中...", end="", flush=True)
            keywords = self.generate_keywords(text, language)
            print(f"\r  ✅ キーワード抽出完了                    ")
            summary = summary + "\n\n---\n\n## 重要キーワード\n\n" + keywords
        except Exception as e:
            print(f"\n  ⚠️  キーワード抽出失敗 (要約のみ保存): {e}")

        # 参考文献抽出
        try:
            print(f"  📚 参考文献抽出中...", end="", flush=True)
            references = self.extract_references(text)
            print(f"\r  ✅ 参考文献抽出完了                    ")
            summary = summary + "\n\n---\n\n## 参考文献\n\n" + references
        except Exception as e:
            print(f"\n  ⚠️  参考文献抽出失敗 (スキップします): {e}")

        # Markdown保存
        summary_path = paper_dir / f"{pdf_path.stem}_summary.md"
        metadata = f"""---
created: {datetime.now().isoformat()}
paper_type: {paper_type}
language: {language if language else 'auto'}
source: [[{pdf_path.name}]]
---

"""
        try:
            summary_path.write_text(metadata + summary, encoding='utf-8')
            print(f"  ✅ 保存完了: {pdf_path.stem}_summary.md")
        except Exception as e:
            print(f"  ❌ 保存エラー: {e}")

def main():
    """メイン処理"""
    # .envファイル読み込み
    load_dotenv()
    
    # 環境変数取得
    api_key = os.getenv("ANTHROPIC_API_KEY")
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    
    if not api_key:
        print("❌ エラー: ANTHROPIC_API_KEYが設定されていません")
        print("   .envファイルにAPI keyを設定してください")
        sys.exit(1)
    
    if not vault_path:
        print("❌ エラー: OBSIDIAN_VAULT_PATHが設定されていません")
        sys.exit(1)
    
    # プロセッサー初期化
    try:
        processor = PaperProcessor(api_key, vault_path)
    except Exception as e:
        print(f"❌ 初期化エラー: {e}")
        sys.exit(1)
    
    # ダウンロードディレクトリ確認
    if not processor.downloads_dir.exists():
        print(f"❌ エラー: downloadsディレクトリが存在しません")
        print(f"   {processor.downloads_dir}")
        sys.exit(1)
    
    # PDFファイル取得
    pdfs = list(processor.downloads_dir.glob("*.pdf"))
    
    if not pdfs:
        print("✅ 処理するPDFはありません")
        print(f"   PDFを {processor.downloads_dir} に配置してください")
        return
    
    # コマンドライン引数の処理
    paper_type = None
    language = 'ja'
    
    # 使用方法の表示
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        print("\n使用方法:")
        print("  python process_papers.py [論文タイプ] [言語]")
        print("\n論文タイプ:")
        print("  empirical    - 実証論文")
        print("  theoretical  - 理論論文")
        print("  review       - レビュー論文")
        print("  (指定なし)   - 自動判定")
        print("\n言語:")
        print("  ja / japanese  - 日本語で要約")
        print("  en / english   - 英語で要約")
        print("  (指定なし)     - 論文の言語に合わせる")
        print("\n例:")
        print("  python process_papers.py empirical ja")
        print("  python process_papers.py theoretical en")
        print("  python process_papers.py review")
        print("  python process_papers.py")
        sys.exit(0)
    
    # 引数パース
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()
        
        # 論文タイプの判定
        if arg_lower in ['empirical', 'theoretical', 'review']:
            paper_type = arg_lower
        
        # 言語の判定
        elif arg_lower in ['ja', 'japanese', '日本語']:
            language = 'ja'
        elif arg_lower in ['en', 'english', '英語']:
            language = 'en'
        
        # 不明な引数
        else:
            print(f"⚠️  警告: 不明な引数 '{arg}'")
            print("   使用方法を確認するには: python process_papers.py --help")
    
    # 指定内容の表示
    if paper_type:
        print(f"\n📌 論文タイプ指定: {paper_type}")
    else:
        print(f"\n📌 論文タイプ: 自動判定")
    
    if language:
        lang_name = "日本語" if language == 'ja' else "英語"
        print(f"🌐 要約言語: {lang_name}")
    else:
        print(f"🌐 要約言語: 論文の言語に合わせる")
    
    # 処理開始
    print(f"\n{'='*60}")
    print(f"📚 {len(pdfs)}本のPDFを処理します")
    print(f"{'='*60}")
    
    success_count = 0
    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}]")
        try:
            processor.process_paper(pdf, paper_type, language)
            success_count += 1
        except Exception as e:
            print(f"  ❌ 予期しないエラー: {e}")
    
    # 完了メッセージ
    print(f"\n{'='*60}")
    print(f"🎉 処理完了！")
    print(f"   成功: {success_count}/{len(pdfs)}本")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
