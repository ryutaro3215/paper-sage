"""Claude Code CLI（claude -p）経由でサブスクリプション枠を使って Claude を呼び出す"""

import os
import subprocess

DEFAULT_TIMEOUT = 1800


def run_claude(system: str, user_content: str, model: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """claude -p を1回呼び出して応答テキストを返す

    - ANTHROPIC_API_KEY 等を環境から除き、API課金ではなくサブスク認証（claude login）を使わせる
    - ツール・CLAUDE.md・スキルを無効化し、純粋なテキスト生成として使う
    - 論文全文は引数長の制限を避けるため stdin で渡す
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt", system,
        "--tools", "",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "text",
    ]
    try:
        proc = subprocess.run(
            cmd, input=user_content, capture_output=True, text=True,
            env=env, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("claude コマンドが見つかりません。Claude Code をインストールし `claude login` してください")
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p が失敗しました (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout.strip()
