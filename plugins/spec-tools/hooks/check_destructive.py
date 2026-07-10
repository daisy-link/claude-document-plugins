#!/usr/bin/env python3
"""spec-tools: 破壊的コマンドを実行前に遮断する PreToolUse フック。

ルール:
- 削除系コマンド（rm / rmdir / shred / find -delete / mv）が
  プロジェクトフォルダ外・一時ディレクトリ外のパスを対象にしていたら拒否（exit 2）
- sudo / dd / mkfs* / diskutil erase系 / shutdown / reboot は場所を問わず一律拒否
- 変数展開などでパスを静的に判定できない場合は「ask」（ユーザーに承認を求める）

拒否時: stderr にメッセージを出して exit 2（コマンドは実行されず、理由がClaudeに伝わる）
ask時 : permissionDecision=ask のJSONをstdoutに出して exit 0
許可時: 何も出さず exit 0
"""
import json
import os
import re
import shlex
import sys

# 場所を問わず一律拒否するコマンド
HARD_DENY = {"sudo", "doas", "dd", "shutdown", "reboot", "halt"}
# パス検査の対象となる削除系コマンド
DELETE_CMDS = {"rm", "rmdir", "shred", "mv"}
# コマンド名の前に付き得るラッパー
WRAPPERS = {"command", "exec", "nohup", "time", "nice", "env"}
# diskutil の破壊的サブコマンド
DISKUTIL_DENY = {"erasedisk", "erasevolume", "reformat", "partitiondisk", "zerodisk"}


def deny(message: str) -> None:
    print(f"[spec-tools hook] ブロックしました: {message}", file=sys.stderr)
    sys.exit(2)


def ask(message: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"[spec-tools hook] {message}",
        }
    }, ensure_ascii=False))
    sys.exit(0)


def allowed_roots(project: str) -> list:
    roots = [os.path.realpath(project)]
    for tmp in ("/tmp", "/private/tmp", "/var/folders", os.environ.get("TMPDIR") or ""):
        if tmp:
            roots.append(os.path.realpath(tmp))
    return roots


def is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def check_paths(cmd: str, path_tokens: list, cwd: str, roots: list) -> None:
    """パス引数を解決し、許可された場所の外を指していたら deny / ask する。"""
    for raw in path_tokens:
        if "$" in raw or "`" in raw:
            ask(f"`{cmd}` の対象パス `{raw}` に変数展開が含まれるため、静的に安全確認できません。"
                "実行してよいか確認してください。")
        expanded = os.path.expanduser(raw)
        resolved = os.path.realpath(os.path.join(cwd, expanded))
        if not any(is_within(resolved, root) for root in roots):
            deny(f"`{cmd}` がプロジェクトフォルダ外のパス `{resolved}` を対象にしています。"
                 "プロジェクト外の削除・移動は禁止されています。必要な場合はユーザーが手動で実行してください。")


def extract_paths(tokens: list) -> list:
    """フラグを除いたパス引数を抽出する（`--` 以降はすべてパスとして扱う）。"""
    paths = []
    after_ddash = False
    for tok in tokens:
        if after_ddash:
            paths.append(tok)
        elif tok == "--":
            after_ddash = True
        elif not tok.startswith("-"):
            paths.append(tok)
    return paths


def check_segment(tokens: list, cwd: str, roots: list) -> None:
    """1つの単純コマンド（パイプ・連結で区切られた単位）を検査する。"""
    # 環境変数代入・ラッパーを読み飛ばして実コマンドを特定する
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        if os.path.basename(tok) in WRAPPERS:
            i += 1
            continue
        break
    if i >= len(tokens):
        return
    cmd = os.path.basename(tokens[i])
    args = tokens[i + 1:]

    if cmd in HARD_DENY:
        deny(f"`{cmd}` はシステムに影響するため一律禁止されています。")
    if cmd.startswith("mkfs"):
        deny("`mkfs` 系コマンド（ディスクのフォーマット）は一律禁止されています。")
    if cmd == "diskutil" and any(a.lower() in DISKUTIL_DENY for a in args):
        deny("`diskutil` の消去・フォーマット系サブコマンドは一律禁止されています。")

    if cmd == "xargs":
        # xargs 経由の削除は対象パスを静的に判定できない
        rest = [os.path.basename(a) for a in args if not a.startswith("-")]
        if any(r in DELETE_CMDS for r in rest):
            ask("`xargs` 経由の削除コマンドは対象パスを静的に確認できません。"
                "実行してよいか確認してください。")
        return

    if cmd in DELETE_CMDS:
        check_paths(cmd, extract_paths(args), cwd, roots)
        return

    if cmd == "find":
        joined = " ".join(args)
        if "-delete" in args or re.search(r"-exec\s+(rm|shred)\b", joined):
            # find の探索起点（最初のフラグより前の非フラグ引数）を検査する
            starts = []
            for a in args:
                if a.startswith("-"):
                    break
                starts.append(a)
            if not starts:
                starts = ["."]
            check_paths("find -delete", starts, cwd, roots)


SEGMENT_OPERATORS = {"|", "||", "&&", ";", "&", "\n"}


def split_segments(command: str) -> list:
    """コマンド文字列を演算子（| || && ; & 改行）で分割し、トークン列のリストを返す。

    shlexで引用符を考慮しながらトークン化してから演算子で区切るため、
    引用符内に `|` `;` `&` 等が含まれていても誤って分割しない
    （例: grep -n "a|b" file.txt | grep -i foo）。
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars="|&;\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        ask("コマンドの構文を解析できませんでした。実行してよいか確認してください。")
        return []
    segments = []
    current = []
    for tok in tokens:
        if tok in SEGMENT_OPERATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        sys.exit(0)
    cwd = data.get("cwd") or os.getcwd()
    project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    roots = allowed_roots(project)
    for tokens in split_segments(command):
        check_segment(tokens, cwd, roots)
    sys.exit(0)


if __name__ == "__main__":
    main()
