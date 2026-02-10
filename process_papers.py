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

class PaperProcessor:
    def __init__(self, api_key, vault_path):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.vault_path = Path(vault_path)
        self.research_dir = self.vault_path / "MyPage/Research"
        self.downloads_dir = self.research_dir / "downloads"
        self.prompts_dir = self.vault_path / "_prompts"
        
        # 論文タイプ別のディレクトリ
        self.paper_dirs = {
            "empirical": self.research_dir / "empirical",
            "theoretical": self.research_dir / "theoretical",
            "review": self.research_dir / "review"
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
        """論文タイプを自動判定"""
        text_lower = text.lower()
        
        # キーワードベースの判定
        empirical_keywords = ['hypothesis', 'hypotheses', 'regression', 'sample', 'data collection', 'statistical', 'coefficient', 'variable']
        theoretical_keywords = ['proposition', 'framework', 'conceptual', 'theorize', 'construct']
        review_keywords = ['literature review', 'systematic review', 'meta-analysis', 'prior research']
        
        # スコアリング
        empirical_score = sum(1 for kw in empirical_keywords if kw in text_lower)
        theoretical_score = sum(1 for kw in theoretical_keywords if kw in text_lower)
        review_score = sum(1 for kw in review_keywords if kw in text_lower)
        
        scores = {
            'empirical': empirical_score,
            'theoretical': theoretical_score,
            'review': review_score
        }
        
        detected_type = max(scores, key=scores.get)
        print(f"  📊 判定スコア - empirical:{empirical_score}, theoretical:{theoretical_score}, review:{review_score}")
        
        return detected_type
    
    def summarize(self, text, paper_type):
        """Claude APIで要約生成（ローディングアニメーション付き）"""
        # 対応するプロンプトを取得
        task_prompt = self.prompts.get(paper_type, self.prompts["empirical"])
        
        # システムプロンプトとタスクプロンプトを結合
        full_prompt = f"""{self.system_prompt}

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
                max_tokens=3000,
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
    
    def process_paper(self, pdf_path, paper_type=None):
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
            summary = self.summarize(text, paper_type)
        except Exception as e:
            print(f"  ❌ 要約エラー: {e}")
            # PDFを元に戻す
            new_pdf_path.rename(pdf_path)
            return
        
        # Markdown保存
        summary_path = paper_dir / "summary.md"
        metadata = f"""---
created: {datetime.now().isoformat()}
paper_type: {paper_type}
source: [[{pdf_path.name}]]
---

"""
        try:
            summary_path.write_text(metadata + summary, encoding='utf-8')
            print(f"  ✅ 保存完了: summary.md")
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
    
    # 論文タイプ指定の確認
    paper_type = None
    if len(sys.argv) > 1:
        specified_type = sys.argv[1].lower()
        if specified_type in ['empirical', 'theoretical', 'review']:
            paper_type = specified_type
            print(f"\n📌 論文タイプ指定: {paper_type}")
        else:
            print(f"⚠️  警告: 不明な論文タイプ '{sys.argv[1]}'")
            print("   有効な値: empirical, theoretical, review")
            print("   自動判定モードで続行します\n")
    
    # 処理開始
    print(f"\n{'='*60}")
    print(f"📚 {len(pdfs)}本のPDFを処理します")
    print(f"{'='*60}")
    
    success_count = 0
    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}]")
        try:
            processor.process_paper(pdf, paper_type)
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
