#!/usr/bin/env python3
"""
Paper Sage - 論文自動要約システム
経営学論文をタイプ別に分類し、Claude APIで要約を生成
"""

from pathlib import Path
from typing import Optional
import anthropic
import httpx
import PyPDF2
from datetime import datetime
import os
from dotenv import load_dotenv
import sys
import threading
import time
import shutil
import re
import json

class PaperProcessor:
    # 要約生成用モデル。論文テキストはこのモデルへの1回の呼び出しでのみ入力する
    SUMMARY_MODEL = "claude-sonnet-5"
    SUMMARY_MAX_TOKENS = 64000

    # 要約本文の後に続けて出力させる付録（重要キーワード）と、frontmatter 用のコンセプト
    APPENDIX_INSTRUCTIONS = """【追加出力】上記の出力形式ルールに加えて、要約本文（最後の項目）の後に、続けて以下のセクションと <concepts> ブロックをこの順で出力してください。本文と同じ言語方針に従ってください。

## 重要キーワード
論文のコア概念・理論・手法に関する重要キーワードを5〜10個、以下のMarkdownテーブルで出力する（論文中で実際に使われている用語を優先する。「日本語訳」列は日本語で書く）。

| キーワード | 日本語訳 | 説明（1文） |
|-----------|----------|-------------|
| (英語キーワード) | (日本語訳) | (その論文文脈での意味・役割を1文で) |

最後に、論文の主要な学術概念を2〜4個、CamelCase（例: CompetitiveStrategy, TopManagementTeam）で <concepts> タグの中に1行1つずつ出力する。タグの外には何も書かないこと。

<concepts>
ConceptOne
ConceptTwo
</concepts>"""

    # 論文タイプ判定用（TypeSafe AI Jev）
    JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
    JEV_MODEL = "jev-latest"
    # state + 最長の質問で32kトークンが上限。日本語は1文字≒1トークンなので余裕を持たせる
    # （タイトル・アブストラクト・序論を含む冒頭部分で十分判定できる）
    JEV_STATE_CHARS = 20000
    JEV_MAX_RETRIES = 3

    def __init__(self, api_key, vault_path, jev_api_key):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.jev_api_key = jev_api_key
        self.vault_path = Path(vault_path)
        self.research_dir = self.vault_path / "MyPage/Management"
        self.downloads_dir = self.research_dir / "_inbox/downloads"
        self.prompts_dir = self.research_dir / "_prompts"

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
                pt: (self.prompts_dir / f"{pt}_simple.md").read_text(encoding='utf-8')
                for pt in ["empirical", "theoretical", "review"]
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
        """TypeSafe AI の Jev で論文タイプを判定し、最も確率の高いタイプを返す"""
        payload = {
            "state": text[:self.JEV_STATE_CHARS],
            "model": self.JEV_MODEL,
            "questions": {
                "paper_type": {
                    "type": "choice",
                    "instructions": "この経営学論文のタイプはどれか？",
                    "criteria": {
                        "empirical": "仮説検証・データ収集・統計分析や定性データ分析を行う実証研究",
                        "theoretical": "理論構築・命題提示・概念フレームワーク提案が中心の理論研究",
                        "review": "既存文献のレビュー・システマティックレビュー・メタ分析が中心",
                    },
                }
            },
        }
        try:
            # 429（レート制限）・529（過負荷）は指数バックオフで再試行する
            for attempt in range(self.JEV_MAX_RETRIES + 1):
                response = httpx.post(
                    self.JEV_ENDPOINT,
                    headers={"Authorization": f"Bearer {self.jev_api_key}"},
                    json=payload,
                    timeout=30,
                )
                if response.status_code not in (429, 529) or attempt == self.JEV_MAX_RETRIES:
                    break
                time.sleep(2 ** attempt)
            response.raise_for_status()
            answer = response.json()["answers"]["paper_type"]
        except httpx.HTTPStatusError as e:
            print(f"  ❌ Jev APIエラー ({e.response.status_code}): {e.response.text[:200]}")
            return None
        except (httpx.HTTPError, KeyError, ValueError) as e:
            print(f"  ❌ Jev API呼び出しに失敗しました: {e}")
            return None

        probabilities = answer["probabilities"]
        print("  🔍 Jev確率: " + ", ".join(f"{k}={v:.2f}" for k, v in probabilities.items()))
        return max(probabilities, key=probabilities.get)

    def _concept_registry_text(self, cache: Optional[dict]) -> str:
        """cache.json の concept_registry を、既存概念を優先選択させる指示文に変換

        参照先: wiki/CLAUDE.md, MyPage/CLAUDE.md の Frontmatter スキーマ
        """
        existing: list[str] = []
        if cache:
            registry = cache.get('concept_registry', {})
            if isinstance(registry, dict):
                for k, v in registry.items():
                    if isinstance(v, list):    # 旧形式: {ドメイン: [概念, ...]}
                        existing.extend(v)
                    else:                      # v2形式: {概念: ドメイン}
                        existing.append(k)
            elif isinstance(registry, list):
                existing = list(registry)
            # 並び順を固定してプロンプトキャッシュを安定させる
            existing = sorted(set(existing))
        if not existing:
            return "登録済み概念一覧: （なし）"
        return (
            "<concepts> に出力する概念は、以下の登録済み概念一覧から優先的に選ぶこと。"
            "一覧にない概念が必要な場合のみ新規作成してよい。\n"
            "登録済み概念一覧:\n" + '\n'.join(f'- {c}' for c in existing)
        )

    def _build_system(self, paper_type, cache):
        """要約・付録・コンセプトを1回で出力させるシステムプロンプトを組み立てる（出力言語は英語固定）"""
        static_system = (
            f"{self.system_prompt}\n\n---\n\n"
            f"{self.prompts[paper_type]}\n\n---\n\n{self.APPENDIX_INSTRUCTIONS}"
        )
        # 論文間で共通のプレフィックスをキャッシュする（最後のブロックに付けると両ブロックが対象）
        return [
            {"type": "text", "text": static_system},
            {"type": "text", "text": self._concept_registry_text(cache),
             "cache_control": {"type": "ephemeral"}},
        ]

    def _stream_message(self, system, user_content, label):
        """Claude API を1回呼び出して全文を返す（ローディングアニメーション付き）

        出力が長いため、HTTPタイムアウトを避けるようストリーミングで受信する。
        """
        print(f"  🤖 {label}", end="", flush=True)
        stop_loading = threading.Event()

        def loading_animation():
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            while not stop_loading.is_set():
                print(f"\r  🤖 {label} {frames[idx % len(frames)]}", end="", flush=True)
                idx += 1
                time.sleep(0.1)

        loading_thread = threading.Thread(target=loading_animation, daemon=True)
        loading_thread.start()

        is_complete = False
        try:
            with self.client.messages.stream(
                model=self.SUMMARY_MODEL,
                max_tokens=self.SUMMARY_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                message = stream.get_final_message()
            if message.stop_reason == "refusal":
                raise RuntimeError("Claude が要約を拒否しました (stop_reason: refusal)")
            result = "".join(b.text for b in message.content if b.type == 'text')
            is_complete = message.stop_reason != "max_tokens"
        finally:
            stop_loading.set()
            loading_thread.join()
            if is_complete:
                print(f"\r  ✅ {label}完了                    ")
            else:
                print(f"\r  ⚠️  {label}: max_tokensに達したか失敗しました")

        usage = message.usage
        print(
            f"  📊 tokens: input={usage.input_tokens:,} "
            f"cache_read={usage.cache_read_input_tokens or 0:,} "
            f"cache_write={usage.cache_creation_input_tokens or 0:,} "
            f"output={usage.output_tokens:,}"
        )
        return result, is_complete

    def summarize(self, text, paper_type, cache=None):
        """要約本文・キーワード・コンセプトを1回のAPI呼び出しで生成（論文は全文を入力）"""
        return self._stream_message(
            self._build_system(paper_type, cache),
            f"論文テキスト:\n{text}",
            "Claude API呼び出し中",
        )

    def _parse_output(self, raw: str):
        """モデル出力から <concepts> を取り出し、付録セクションの区切り線を整える

        Returns: (本文, concepts のリスト)
        """
        concepts = []
        match = re.search(r'<concepts>(.*?)</concepts>', raw, re.DOTALL)
        if match:
            concepts = [l.strip().lstrip('- ').strip() for l in match.group(1).splitlines()]
            concepts = [c for c in concepts if c]
            raw = raw[:match.start()] + raw[match.end():]
        body = raw.strip()
        # 付録セクションの前は常に「---」1本で区切る
        body = re.sub(r'\n+(?:---\n+)?(?=## 重要キーワード\n)', '\n\n---\n\n', body)
        return body, concepts

    def load_cache(self) -> dict:
        """wiki/cache.json を読み込む（存在しない場合は空dictを返す）

        concept_registry: 既存の概念ノード一覧（concept フィールドの選択肢）
        tag_registry:     登録済みタグ一覧（tags フィールドの選択肢）
        参照先: wiki/CLAUDE.md, MyPage/CLAUDE.md の Frontmatter スキーマ
        """
        cache_path = self.vault_path / "wiki/cache.json"
        if not cache_path.exists():
            return {}
        try:
            return json.loads(cache_path.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"  ⚠️  cache.json 読み込み失敗: {e}")
            return {}

    def _strip_frontmatter(self, content: str) -> str:
        """YAMLフロントマターを除去して本文を返す"""
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return content

    REFERENCES_MARKER = '\n\n---\n\n## 参考文献'

    def _split_main_and_appended(self, body: str):
        """モデルが生成した部分と、コードが追記した参考文献セクションを分離して返す"""
        idx = body.find(self.REFERENCES_MARKER)
        if idx != -1:
            return body[:idx], body[idx:]
        return body, ''

    def resume_summary(self, text, paper_type, existing_summary, cache=None):
        """途中で切れた出力の続きを生成（システムプロンプトは通常時と共通にしてキャッシュを再利用）"""
        return self._stream_message(
            self._build_system(paper_type, cache),
            (
                f"論文テキスト:\n{text}\n\n"
                f"【既存の出力（途中まで）】:\n{existing_summary}\n\n"
                "【追加指示】上記の既存の出力は途中で切れています。"
                "指定フォーマットに基づき、未記載・未完成のセクション（付録と <concepts> を含む）のみを続きから出力してください。"
                "既存の内容は繰り返さないこと。"
            ),
            "続きを生成中",
        )

    def find_incomplete_summaries(self) -> list:
        """status: incomplete のサマリーファイルを収集"""
        incomplete = []
        for pt_dir in self.paper_dirs.values():
            for summary_path in sorted(pt_dir.rglob("*_summary.md")):
                content = summary_path.read_text(encoding='utf-8')
                if re.search(r'^status:\s*incomplete', content, re.MULTILINE):
                    incomplete.append(summary_path)
        return incomplete

    def resume_paper(self, summary_path: Path) -> bool:
        """途中で切れたサマリーの続きを生成してファイルを更新"""
        print(f"\n{'='*60}")
        print(f"🔄 リジューム: {summary_path.name}")
        print(f"{'='*60}")

        paper_dir = summary_path.parent
        pdfs = list(paper_dir.glob("*.pdf"))
        if not pdfs:
            print(f"  ⚠️  PDFが見つかりません。スキップします。")
            return False
        pdf_path = pdfs[0]

        existing_content = summary_path.read_text(encoding='utf-8')
        type_match = re.search(r'^paper_type:\s*(\S+)', existing_content, re.MULTILINE)
        paper_type = type_match.group(1) if type_match else 'empirical'

        try:
            text = self.extract_text(pdf_path)
            print(f"  ✅ テキスト抽出完了: {len(text):,} 文字")
        except Exception as e:
            print(f"  ❌ PDF読み込みエラー: {e}")
            return False

        body = self._strip_frontmatter(existing_content)
        main_summary, appended = self._split_main_and_appended(body)

        try:
            continuation, is_complete = self.resume_summary(
                text, paper_type, main_summary, self.load_cache())
        except Exception as e:
            print(f"  ❌ 続き生成エラー: {e}")
            return False

        generated, concepts = self._parse_output(main_summary + '\n\n' + continuation)
        full_summary = generated + appended
        status = 'complete' if is_complete else 'incomplete'

        # 既存 frontmatter を保持し status（と続きで得られた concept）のみ更新
        if existing_content.startswith('---'):
            parts = existing_content.split('---', 2)
            fm_block = parts[1]
            if re.search(r'^status:', fm_block, re.MULTILINE):
                fm_block = re.sub(r'^status:\s*\S+', f'status: {status}', fm_block, count=1, flags=re.MULTILINE)
            else:
                fm_block += f'status: {status}\n'
            if concepts:
                concept_yaml = ''.join(f'  - {c}\n' for c in concepts)
                fm_block = re.sub(r'^concept:\n(?:  - .*\n)*', f'concept:\n{concept_yaml}',
                                  fm_block, count=1, flags=re.MULTILINE)
            new_file_content = '---' + fm_block + '---\n\n' + full_summary
        else:
            new_file_content = full_summary

        try:
            summary_path.write_text(new_file_content, encoding='utf-8')
            print(f"  ✅ 更新完了: {summary_path.name} (status: {status})")
            return True
        except Exception as e:
            print(f"  ❌ 保存エラー: {e}")
            return False

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

    def rescue_paper(self, pdf_path: Path, paper_type: str) -> bool:
        """PDF処理失敗時にディレクトリを作成してPDFを移動し、フロントマターのみのスタブサマリーを生成する"""
        print(f"\n{'='*60}")
        print(f"🚑 レスキュー処理: {pdf_path.name}")
        print(f"  タイプ: {paper_type}")
        print(f"{'='*60}")

        target_dir = self.paper_dirs[paper_type]
        paper_dir = target_dir / pdf_path.stem
        paper_dir.mkdir(parents=True, exist_ok=True)
        print(f"  📁 ディレクトリ作成: {paper_dir.relative_to(self.vault_path)}")

        new_pdf_path = paper_dir / pdf_path.name
        try:
            pdf_path.rename(new_pdf_path)
            print(f"  📦 PDF移動完了")
        except Exception as e:
            print(f"  ❌ PDF移動エラー: {e}")
            return False

        summary_path = paper_dir / f"{pdf_path.stem}_summary.md"
        paper_type_tag = {
            'empirical': 'Paper/Empirical',
            'theoretical': 'Paper/Theoretical',
            'review': 'Paper/Review',
        }.get(paper_type, 'Paper/Empirical')
        metadata = f"""---
domain: Management
concept:
  - Management
tags:
  - Memo/AI
  - Type/Fact
  - Source/Paper
  - {paper_type_tag}
created: {datetime.now().isoformat()}
paper_type: {paper_type}
language: en
detail: simple
source: "[[{pdf_path.name}]]"
status: unprocessable
---
"""
        try:
            summary_path.write_text(metadata, encoding='utf-8')
            print(f"  ✅ スタブサマリー作成: {pdf_path.stem}_summary.md")
            return True
        except Exception as e:
            print(f"  ❌ サマリー作成エラー: {e}")
            return False

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
        
        # 要約・キーワード・コンセプトを1回の呼び出しで生成
        # （cache.json の concept_registry から既存概念を優先選択させる）
        try:
            raw, is_complete = self.summarize(text, paper_type, self.load_cache())
        except Exception as e:
            print(f"  ❌ 要約エラー: {e}")
            # PDFを元に戻す
            new_pdf_path.rename(pdf_path)
            return
        summary, concepts = self._parse_output(raw)

        # 参考文献抽出（API呼び出しなし）
        try:
            print(f"  📚 参考文献抽出中...", end="", flush=True)
            references = self.extract_references(text)
            print(f"\r  ✅ 参考文献抽出完了                    ")
            summary = summary + self.REFERENCES_MARKER + "\n\n" + references
        except Exception as e:
            print(f"\n  ⚠️  参考文献抽出失敗 (スキップします): {e}")

        # Markdown保存
        summary_path = paper_dir / f"{pdf_path.stem}_summary.md"
        status = 'complete' if is_complete else 'incomplete'
        if not is_complete:
            print(f"  ⚠️  要約が途中で切れています。次回実行時に自動補完します。")
        paper_type_tag = {
            'empirical': 'Paper/Empirical',
            'theoretical': 'Paper/Theoretical',
            'review': 'Paper/Review',
        }.get(paper_type, 'Paper/Empirical')
        concept_yaml = '\n'.join(f'  - {c}' for c in concepts) if concepts else '  - Management'
        metadata = f"""---
domain: Management
concept:
{concept_yaml}
tags:
  - Memo/AI
  - Type/Fact
  - Source/Paper
  - {paper_type_tag}
created: {datetime.now().isoformat()}
paper_type: {paper_type}
language: en
detail: simple
source: "[[{pdf_path.name}]]"
status: {status}
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
    jev_api_key = os.getenv("JEV_API_KEY")
    
    if not api_key:
        print("❌ エラー: ANTHROPIC_API_KEYが設定されていません")
        print("   .envファイルにAPI keyを設定してください")
        sys.exit(1)
    
    if not vault_path:
        print("❌ エラー: OBSIDIAN_VAULT_PATHが設定されていません")
        sys.exit(1)
    
    if not jev_api_key:
        print("❌ エラー: JEV_API_KEYが設定されていません")
        sys.exit(1)
    
    # プロセッサー初期化
    try:
        processor = PaperProcessor(api_key, vault_path, jev_api_key)
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
    rescue_mode = False

    # 使用方法の表示
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        print("\n使用方法:")
        print("  python process_papers.py [論文タイプ] [--rescue]")
        print("\n論文タイプ:")
        print("  empirical    - 実証論文")
        print("  theoretical  - 理論論文")
        print("  review       - レビュー論文")
        print("  (指定なし)   - 自動判定（通常モードのみ）")
        print("\nオプション:")
        print("  --rescue     - PDFを指定タイプに移動してフロントマターのみのサマリーを作成（要約しない）")
        print("                 論文タイプの指定が必須")
        print("\n例:")
        print("  python process_papers.py empirical")
        print("  python process_papers.py review")
        print("  python process_papers.py")
        print("  python process_papers.py empirical --rescue")
        sys.exit(0)

    # 引数パース
    for arg in sys.argv[1:]:
        arg_lower = arg.lower()

        # レスキューモード
        if arg_lower == '--rescue':
            rescue_mode = True

        # 論文タイプの判定
        elif arg_lower in ['empirical', 'theoretical', 'review']:
            paper_type = arg_lower

        # 不明な引数
        else:
            print(f"⚠️  警告: 不明な引数 '{arg}'")
            print("   使用方法を確認するには: python process_papers.py --help")

    # レスキューモードの場合は論文タイプ必須
    if rescue_mode and paper_type is None:
        print("❌ エラー: --rescue モードでは論文タイプ (empirical/theoretical/review) の指定が必須です")
        print("   例: python process_papers.py empirical --rescue")
        sys.exit(1)

    if rescue_mode:
        print(f"\n🚑 レスキューモード: {paper_type}")
        print(f"{'='*60}")
        print(f"🚑 {len(pdfs)}本のPDFをレスキュー処理します")
        print(f"{'='*60}")

        success_count = 0
        for i, pdf in enumerate(pdfs, 1):
            print(f"\n[{i}/{len(pdfs)}]")
            try:
                if processor.rescue_paper(pdf, paper_type):
                    success_count += 1
            except Exception as e:
                print(f"  ❌ 予期しないエラー: {e}")

        print(f"\n{'='*60}")
        print(f"🎉 レスキュー完了！")
        print(f"   成功: {success_count}/{len(pdfs)}本")
        print(f"{'='*60}\n")
        return

    # 指定内容の表示
    if paper_type:
        print(f"\n📌 論文タイプ指定: {paper_type}")
    else:
        print(f"\n📌 論文タイプ: 自動判定")

    print(f"🌐 要約言語: 英語")

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

    # 途中で切れたサマリーの自動補完
    incomplete = processor.find_incomplete_summaries()
    if not incomplete:
        return

    print(f"\n{'='*60}")
    print(f"🔄 未完了の要約を {len(incomplete)} 件検出。続きを補完します...")
    print(f"{'='*60}")
    resume_success = 0
    for i, summary_path in enumerate(incomplete, 1):
        print(f"\n[{i}/{len(incomplete)}]")
        try:
            if processor.resume_paper(summary_path):
                resume_success += 1
        except Exception as e:
            print(f"  ❌ 予期しないエラー: {e}")

    print(f"\n{'='*60}")
    print(f"🎉 補完完了！ 成功: {resume_success}/{len(incomplete)} 件")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
