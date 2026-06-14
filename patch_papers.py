"""
Paper Sage - 既存サマリーへのキーワード・参考文献追記スクリプト

empirical / theoretical / review ディレクトリ内の *_summary.md のうち
## 重要キーワード セクションが存在しないものに対して、
同ディレクトリの PDF からキーワードと参考文献を抽出して追記する。
"""

from __future__ import annotations

from pathlib import Path
import anthropic
import PyPDF2
import os
import re
import sys
import time
import threading
from dotenv import load_dotenv


class PaperPatcher:
    def __init__(self, api_key: str, research_dir: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.research_dir = Path(research_dir)
        self.paper_dirs = [
            self.research_dir / "empirical",
            self.research_dir / "theoretical",
            self.research_dir / "review",
        ]

    # ------------------------------------------------------------------
    # テキスト抽出
    # ------------------------------------------------------------------
    def extract_text(self, pdf_path: Path) -> str:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            return "".join(page.extract_text() or "" for page in reader.pages)

    # ------------------------------------------------------------------
    # キーワード生成（process_papers.py と同ロジック）
    # ------------------------------------------------------------------
    def generate_keywords(self, text: str, language: str | None = None) -> str:
        lang_instruction = ""
        if language == "en":
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
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------
    # 参考文献抽出（process_papers.py と同ロジック）
    # ------------------------------------------------------------------
    def extract_references(self, text: str) -> str:
        pattern = re.compile(
            r"(?m)^[ \t]*(REFERENCES|References|BIBLIOGRAPHY|Bibliography|WORKS CITED|Works Cited)[ \t]*$"
        )
        matches = list(pattern.finditer(text))

        if matches:
            ref_start = matches[-1].start()
            refs_text = text[ref_start:]
            print(f"\n  📍 参考文献セクション検出（テキスト位置: {ref_start:,}文字目）", end="", flush=True)
        else:
            refs_text = None

        if not refs_text:
            return "（参考文献セクションが見つかりませんでした）"

        prompt = f"""以下は論文の参考文献セクションです。各文献を箇条書きリスト形式（"- " で始める）で1行ずつ出力してください。

- 見出し行（REFERENCESなど）は除いてください
- リスト以外のテキストは一切出力しないこと

参考文献セクション:
{refs_text}
"""
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------
    # 処理対象サマリーを収集
    # ------------------------------------------------------------------
    def find_targets(self) -> list[Path]:
        targets = []
        for d in self.paper_dirs:
            if not d.exists():
                continue
            for summary_path in sorted(d.rglob("*_summary.md")):
                content = summary_path.read_text(encoding="utf-8")
                if "## 重要キーワード" not in content or "## 参考文献" not in content:
                    targets.append(summary_path)
        return targets

    # ------------------------------------------------------------------
    # サマリーに対応する PDF を探す
    # ------------------------------------------------------------------
    def find_pdf(self, summary_path: Path) -> Path | None:
        paper_dir = summary_path.parent
        # summary ファイル名から stem を取得: <PaperTitle>_summary -> <PaperTitle>
        stem = summary_path.stem  # e.g. "SomePaper_summary"
        paper_stem = re.sub(r"_summary$", "", stem)

        # 完全一致を優先、次に任意のPDF
        exact = paper_dir / f"{paper_stem}.pdf"
        if exact.exists():
            return exact

        pdfs = list(paper_dir.glob("*.pdf"))
        if pdfs:
            return pdfs[0]

        return None

    # ------------------------------------------------------------------
    # 1件処理
    # ------------------------------------------------------------------
    def patch_one(self, summary_path: Path) -> bool:
        print(f"\n{'='*60}")
        print(f"📄 対象: {summary_path.relative_to(self.research_dir)}")
        print(f"{'='*60}")

        pdf_path = self.find_pdf(summary_path)
        if pdf_path is None:
            print("  ⚠️  対応するPDFが見つかりません。スキップします。")
            return False

        print(f"  📎 PDF: {pdf_path.name}")

        # テキスト抽出
        try:
            text = self.extract_text(pdf_path)
            print(f"  ✅ テキスト抽出完了: {len(text):,} 文字")
        except Exception as e:
            print(f"  ❌ PDF読み込みエラー: {e}")
            return False

        # 言語判定（フロントマターから取得）
        content = summary_path.read_text(encoding="utf-8")
        lang_match = re.search(r"^language:\s*(\S+)", content, re.MULTILINE)
        language = lang_match.group(1) if lang_match else None

        # キーワード生成（セクションが存在しない場合のみ）
        append_text = ""
        if "## 重要キーワード" not in content:
            try:
                print("  🔑 キーワード抽出中...", end="", flush=True)
                keywords = self.generate_keywords(text, language)
                print("\r  ✅ キーワード抽出完了                    ")
                append_text += "\n\n---\n\n## 重要キーワード\n\n" + keywords
            except Exception as e:
                print(f"\n  ⚠️  キーワード抽出失敗: {e}")
        else:
            print("  ⏭️  キーワードセクション既存のためスキップ")

        # 参考文献抽出（セクションが存在しない場合のみ）
        if "## 参考文献" not in content:
            try:
                print("  📚 参考文献抽出中...", end="", flush=True)
                references = self.extract_references(text)
                print("\r  ✅ 参考文献抽出完了                    ")
                append_text += "\n\n---\n\n## 参考文献\n\n" + references
            except Exception as e:
                print(f"\n  ⚠️  参考文献抽出失敗: {e}")
        else:
            print("  ⏭️  参考文献セクション既存のためスキップ")

        if not append_text:
            print("  ⚠️  追記するコンテンツがありません。スキップします。")
            return False

        # 追記
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            print(f"  ✅ 追記完了: {summary_path.name}")
            return True
        except Exception as e:
            print(f"  ❌ 書き込みエラー: {e}")
            return False

    # ------------------------------------------------------------------
    # 全件処理
    # ------------------------------------------------------------------
    def run(self):
        targets = self.find_targets()

        if not targets:
            print("✅ 処理対象のサマリーはありません（全ファイルにキーワードセクションが存在します）")
            return

        print(f"\n{'='*60}")
        print(f"🔍 処理対象: {len(targets)} 件")
        for t in targets:
            print(f"   - {t.relative_to(self.research_dir)}")
        print(f"{'='*60}")

        success = 0
        for i, target in enumerate(targets, 1):
            print(f"\n[{i}/{len(targets)}]")
            try:
                if self.patch_one(target):
                    success += 1
            except Exception as e:
                print(f"  ❌ 予期しないエラー: {e}")

        print(f"\n{'='*60}")
        print(f"🎉 完了！ 成功: {success}/{len(targets)} 件")
        print(f"{'='*60}\n")


def main():
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")

    if not api_key:
        print("❌ エラー: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    if not vault_path:
        print("❌ エラー: OBSIDIAN_VAULT_PATH が設定されていません")
        sys.exit(1)

    research_dir = Path(vault_path) / "MyPage/Research"
    if not research_dir.exists():
        print(f"❌ エラー: Research ディレクトリが見つかりません: {research_dir}")
        sys.exit(1)

    patcher = PaperPatcher(api_key, research_dir)

    # ファイル指定がある場合はそのファイルのみ処理
    if len(sys.argv) > 1:
        targets = []
        for arg in sys.argv[1:]:
            p = research_dir / arg
            if not p.exists():
                print(f"❌ ファイルが見つかりません: {p}")
                sys.exit(1)
            targets.append(p)
        success = 0
        for i, target in enumerate(targets, 1):
            print(f"\n[{i}/{len(targets)}]")
            try:
                if patcher.patch_one(target):
                    success += 1
            except Exception as e:
                print(f"  ❌ 予期しないエラー: {e}")
        print(f"\n{'='*60}")
        print(f"🎉 完了！ 成功: {success}/{len(targets)} 件")
        print(f"{'='*60}\n")
    else:
        patcher.run()


if __name__ == "__main__":
    main()
