#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
svn_mcp_server.py  —  日本語パス対応・認証対応の単一ファイル SVN MCP サーバ

設計のポイント（Node版の良さ＋既存Python版のバグ修正）:
  * subprocess を「引数リスト + shell=False」で起動 → Windows では CreateProcessW
    に Unicode で渡るため、日本語パス/URL が文字化けしない。
  * 出力は bytes で受け取り UTF-8(errors="replace") で明示デコード →
    コンソールのコードページ(CP932)に依存しない。
  * 構造化が必要なコマンド(info/status/log/list) は `--xml` を使い ElementTree で解析
    → 既存Python版の「log:null」「whoami:'V 12'」「status の NoneType.splitlines」を解消。
  * 認証は環境変数 SVN_USERNAME/SVN_PASSWORD を --username/--password で明示付与し、
    常に --non-interactive。Node版同様 SVN_PATH / SVN_WORKING_DIRECTORY / SVN_TIMEOUT に対応。
  * 出力が空（クリーンな working copy 等）でもクラッシュしない。

環境変数:
  SVN_PATH               svn 実行ファイル（既定 "svn"）
  SVN_WORKING_DIRECTORY  既定の作業ディレクトリ（既定 プロセスの cwd）
  SVN_USERNAME           認証ユーザー
  SVN_PASSWORD           認証パスワード
  SVN_CONFIG_DIR         svn の --config-dir
  SVN_TRUST_SERVER_CERT  "1"/"true" で自己署名証明書等を許可
  SVN_TIMEOUT            タイムアウト(ミリ秒, 既定 120000)
"""

import os
import sys
import re
import subprocess
import xml.etree.ElementTree as ET

# ---- FastMCP の取り込み（SDK版 / スタンドアロン版どちらでも動くように）----
try:
    from mcp.server.fastmcp import FastMCP  # 公式 SDK 同梱版
except Exception:  # pragma: no cover
    from fastmcp import FastMCP  # スタンドアロン fastmcp

from typing import Optional, List, Union

mcp = FastMCP("svn")

# ---------------------------------------------------------------------------
# 設定（環境変数）
# ---------------------------------------------------------------------------
SVN_PATH = os.environ.get("SVN_PATH", "svn")
SVN_WORKDIR = os.environ.get("SVN_WORKING_DIRECTORY") or os.getcwd()
SVN_USERNAME = os.environ.get("SVN_USERNAME") or ""
SVN_PASSWORD = os.environ.get("SVN_PASSWORD") or ""
SVN_CONFIG_DIR = os.environ.get("SVN_CONFIG_DIR") or ""
SVN_TRUST = (os.environ.get("SVN_TRUST_SERVER_CERT", "").lower() in ("1", "true", "yes"))
try:
    SVN_TIMEOUT = float(os.environ.get("SVN_TIMEOUT", "120000")) / 1000.0
except ValueError:
    SVN_TIMEOUT = 120.0


def _global_args(use_auth: bool = True) -> List[str]:
    """全コマンド共通の安全なグローバルオプション。"""
    args: List[str] = ["--non-interactive"]
    if use_auth:
        if SVN_USERNAME:
            args += ["--username", SVN_USERNAME]
        if SVN_PASSWORD:
            args += ["--password", SVN_PASSWORD]
    if SVN_CONFIG_DIR:
        args += ["--config-dir", SVN_CONFIG_DIR]
    if SVN_TRUST:
        args += [
            "--trust-server-cert-failures",
            "unknown-ca,cn-mismatch,expired,not-yet-valid,other",
        ]
    return args


def run_svn(args: List[str], cwd: Optional[str] = None, use_auth: bool = True):
    """svn を引数リストで実行し (stdout, stderr) を UTF-8 文字列で返す。

    戻り値: (stdout:str, error:str)  error が "" なら成功。
    日本語の path/url は args の要素としてそのまま渡す（Unicode 保持）。
    """
    cmd = [SVN_PATH] + list(args) + _global_args(use_auth)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or SVN_WORKDIR,
            capture_output=True,
            timeout=SVN_TIMEOUT,
            shell=False,
        )
    except FileNotFoundError:
        return "", f"SVN executable not found: {SVN_PATH}"
    except subprocess.TimeoutExpired:
        return "", f"SVN command timed out after {SVN_TIMEOUT:.0f}s"
    except Exception as ex:  # pragma: no cover
        return "", f"Failed to run svn: {ex}"

    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return out, (err.strip() or f"svn exited with code {proc.returncode}")
    return out, ""


def _parse_xml(out: str):
    try:
        return ET.fromstring(out), ""
    except ET.ParseError as ex:
        return None, f"XML parse error: {ex}"


# ---------------------------------------------------------------------------
# 参照系ツール
# ---------------------------------------------------------------------------
@mcp.tool()
def svn_version() -> dict:
    """インストールされている svn クライアントのバージョンを返す。"""
    out, err = run_svn(["--version", "--quiet"], use_auth=False)
    return {"version": out.strip(), "error": err}


@mcp.tool()
def svn_health_check() -> dict:
    """svn の利用可否と作業ディレクトリが working copy かを点検する。"""
    ver, verr = run_svn(["--version", "--quiet"], use_auth=False)
    info_out, ierr = run_svn(["info", "--xml"])
    is_wc = (ierr == "" and "<entry" in info_out)
    return {
        "svn_available": verr == "" and bool(ver.strip()),
        "svn_version": ver.strip(),
        "working_directory": SVN_WORKDIR,
        "is_working_copy": is_wc,
        "username_configured": bool(SVN_USERNAME),
        "error": verr or (ierr if not is_wc else ""),
    }


@mcp.tool()
def svn_whoami() -> dict:
    """現在の SVN ユーザー名を返す（SVN_USERNAME 優先、無ければ svn auth から推定）。"""
    if SVN_USERNAME:
        return {"username": SVN_USERNAME, "source": "env", "error": ""}
    # svn 1.12+ の `svn auth` 出力から Username: 行を拾う
    out, err = run_svn(["auth"], use_auth=False)
    username = ""
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("username:"):
            username = line.split(":", 1)[1].strip()
            break
    return {"username": username, "source": "auth-cache", "error": err if not username else ""}


@mcp.tool()
def svn_info(path: Optional[str] = None) -> dict:
    """working copy または指定パス/URL の詳細情報を返す。"""
    args = ["info", "--xml"]
    if path:
        args.append(path)
    out, err = run_svn(args)
    if err and not out:
        return {"entries": [], "error": err}
    root, perr = _parse_xml(out)
    if root is None:
        return {"entries": [], "raw": out, "error": perr}
    entries = []
    for e in root.findall("entry"):
        repo = e.find("repository")
        commit = e.find("commit")
        wcroot_el = e.find("wc-info/wcroot-abspath")
        entries.append({
            "path": e.get("path"),
            "kind": e.get("kind"),
            "revision": e.get("revision"),
            "url": e.findtext("url") or "",
            "relative_url": e.findtext("relative-url") or "",
            "repository_root": (repo.findtext("root") if repo is not None else "") or "",
            "repository_uuid": (repo.findtext("uuid") if repo is not None else "") or "",
            "last_changed_rev": (commit.get("revision") if commit is not None else "") or "",
            "last_changed_author": (commit.findtext("author") if commit is not None else "") or "",
            "last_changed_date": (commit.findtext("date") if commit is not None else "") or "",
            "wc_root": (wcroot_el.text if wcroot_el is not None else ""),
        })
    return {"entries": entries, "error": err}


@mcp.tool()
def svn_status(path: Optional[str] = None, show_all: bool = False) -> dict:
    """working copy のファイル状態を返す（出力が空でもクラッシュしない）。"""
    args = ["status", "--xml"]
    if show_all:
        args.append("--show-updates")
    if path:
        args.append(path)
    out, err = run_svn(args)
    if err and not out:
        return {"items": [], "error": err}
    root, perr = _parse_xml(out)
    if root is None:
        return {"items": [], "raw": out, "error": perr}
    items = []
    for entry in root.iter("entry"):
        wc = entry.find("wc-status")
        items.append({
            "path": entry.get("path"),
            "item": (wc.get("item") if wc is not None else ""),
            "props": (wc.get("props") if wc is not None else ""),
            "revision": (wc.get("revision") if wc is not None else ""),
        })
    return {"items": items, "count": len(items), "error": err}


@mcp.tool()
def svn_log(repo_path: Optional[str] = None, limit: Optional[int] = None,
            revision: Optional[str] = None) -> dict:
    """リポジトリのコミット履歴を返す（--xml 解析）。"""
    args = ["log", "--xml"]
    if limit:
        args += ["-l", str(int(limit))]
    if revision:
        args += ["-r", str(revision)]
    if repo_path:
        args.append(repo_path)
    out, err = run_svn(args)
    if err and not out:
        return {"entries": [], "error": err}
    root, perr = _parse_xml(out)
    if root is None:
        return {"entries": [], "raw": out, "error": perr}
    entries = []
    for e in root.findall("logentry"):
        paths = []
        plist = e.find("paths")
        if plist is not None:
            for p in plist.findall("path"):
                paths.append({"action": p.get("action"), "path": (p.text or "")})
        entries.append({
            "revision": e.get("revision"),
            "author": e.findtext("author") or "",
            "date": e.findtext("date") or "",
            "message": e.findtext("msg") or "",
            "paths": paths,
        })
    return {"entries": entries, "count": len(entries), "error": err}


@mcp.tool()
def svn_diff(repo_path: Optional[str] = None, revision: Optional[str] = None,
             old_revision: Optional[str] = None, new_revision: Optional[str] = None) -> dict:
    """差分を返す。revision（単一/範囲）または old/new リビジョン指定に対応。"""
    args = ["diff"]
    if old_revision and new_revision:
        args += ["-r", f"{old_revision}:{new_revision}"]
    elif revision:
        args += ["-r", str(revision)]
    if repo_path:
        args.append(repo_path)
    out, err = run_svn(args)
    if err and not out:
        return {"diff": "", "error": err}
    return {"diff": out, "error": err}


@mcp.tool()
def svn_list(path_or_url: Optional[str] = None, recursive: bool = False) -> dict:
    """ディレクトリ/URL のエントリ一覧を返す（--xml 解析）。"""
    args = ["list", "--xml"]
    if recursive:
        args.append("--recursive")
    if path_or_url:
        args.append(path_or_url)
    out, err = run_svn(args)
    if err and not out:
        return {"entries": [], "error": err}
    root, perr = _parse_xml(out)
    if root is None:
        return {"entries": [], "raw": out, "error": perr}
    entries = []
    for e in root.iter("entry"):
        commit = e.find("commit")
        entries.append({
            "kind": e.get("kind"),
            "name": e.findtext("name") or "",
            "size": e.findtext("size") or "",
            "revision": (commit.get("revision") if commit is not None else "") or "",
            "author": (commit.findtext("author") if commit is not None else "") or "",
        })
    return {"entries": entries, "count": len(entries), "error": err}


# ---------------------------------------------------------------------------
# 取得・更新系ツール
# ---------------------------------------------------------------------------
@mcp.tool()
def svn_checkout(url: str, path: Optional[str] = None,
                 revision: Optional[Union[str, int]] = None,
                 depth: Optional[str] = None, force: bool = False,
                 ignore_externals: bool = False) -> dict:
    """リポジトリ URL をローカルにチェックアウトする。"""
    args = ["checkout"]
    if revision is not None:
        args += ["-r", str(revision)]
    if depth:
        args += ["--depth", depth]
    if force:
        args.append("--force")
    if ignore_externals:
        args.append("--ignore-externals")
    args.append(url)
    if path:
        args.append(path)
    out, err = run_svn(args)
    return {"output": out, "error": err}


@mcp.tool()
def svn_update(path: Optional[str] = None,
               revision: Optional[Union[str, int]] = None,
               force: bool = False, ignore_externals: bool = False,
               accept_conflicts: Optional[str] = None,
               set_depth: Optional[str] = None) -> dict:
    """working copy をリポジトリから更新する（sparse の depth 拡張は set_depth で）。"""
    args = ["update"]
    if revision is not None:
        args += ["-r", str(revision)]
    if force:
        args.append("--force")
    if ignore_externals:
        args.append("--ignore-externals")
    if accept_conflicts:
        args += ["--accept", accept_conflicts]
    if set_depth:
        args += ["--set-depth", set_depth]
    if path:
        args.append(path)
    out, err = run_svn(args)
    return {"output": out, "error": err}


@mcp.tool()
def svn_cleanup(path: Optional[str] = None) -> dict:
    """中断された操作の working copy をクリーンアップする。"""
    args = ["cleanup"]
    if path:
        args.append(path)
    out, err = run_svn(args)
    return {"output": out, "error": err}


# ---------------------------------------------------------------------------
# 書き込み系ツール
#   ※ ユーザーの明示ポリシー: ソース管理(SVN/Git)へのコミットは必ず本人のレビューが必要。
#     svn_commit / svn_delete(リポジトリ直接) は、実行前に必ず利用者のレビュー・承認を得ること。
# ---------------------------------------------------------------------------
@mcp.tool()
def svn_add(paths: Union[str, List[str]], force: bool = False,
            parents: bool = False, no_ignore: bool = False) -> dict:
    """ファイル/ディレクトリをバージョン管理に追加する（コミットはしない）。"""
    args = ["add"]
    if force:
        args.append("--force")
    if parents:
        args.append("--parents")
    if no_ignore:
        args.append("--no-ignore")
    args += ([paths] if isinstance(paths, str) else list(paths))
    out, err = run_svn(args)
    return {"output": out, "error": err}


@mcp.tool()
def svn_commit(message: str, paths: Optional[List[str]] = None) -> dict:
    """変更をコミットする。

    【重要】利用者の明示ポリシーにより、SVN へのコミットは必ず本人のレビュー・承認が必要。
    本ツールは承認済みの場合のみ使用すること。
    """
    args = ["commit", "-m", message]
    if paths:
        args += list(paths)
    out, err = run_svn(args)
    return {"output": out, "error": err, "note": "コミットはレビュー必須ポリシー対象です。"}


@mcp.tool()
def svn_delete(paths: Union[str, List[str]], message: Optional[str] = None,
               force: bool = False, keep_local: bool = False) -> dict:
    """ファイルを削除する。URL 直接削除＝リポジトリへの書き込みなのでレビュー必須。"""
    args = ["delete"]
    if force:
        args.append("--force")
    if keep_local:
        args.append("--keep-local")
    if message:
        args += ["-m", message]
    args += ([paths] if isinstance(paths, str) else list(paths))
    out, err = run_svn(args)
    return {"output": out, "error": err, "note": "リポジトリ直接削除はレビュー必須ポリシー対象です。"}


@mcp.tool()
def svn_revert(paths: Union[str, List[str]], recursive: bool = False) -> dict:
    """ローカルの変更を元に戻す。"""
    args = ["revert"]
    if recursive:
        args.append("--recursive")
    args += ([paths] if isinstance(paths, str) else list(paths))
    out, err = run_svn(args)
    return {"output": out, "error": err}


# ---------------------------------------------------------------------------
# コード調査支援ツール（ローカル作業コピーの読取・全文検索）
# ---------------------------------------------------------------------------
# 既定で検索対象とするテキスト系拡張子
_TEXT_EXTS = {
    ".java", ".js", ".jsp", ".jspx", ".tag", ".ftl", ".vue", ".ts",
    ".xml", ".properties", ".sql", ".html", ".htm", ".css", ".json",
    ".yaml", ".yml", ".txt", ".md", ".py",
}


@mcp.tool()
def svn_cat(path: str, revision: Optional[str] = None, max_bytes: int = 1000000) -> dict:
    """ファイル内容を返す。

    ローカル作業コピーに実ファイルがあればそれを UTF-8 で読み、無ければ
    `svn cat`（working copy パス or リポジトリ URL）で取得する。
    revision を指定した場合は常に `svn cat -r` を使う。
    """
    if revision is None and os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                data = f.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            text = data[:max_bytes].decode("utf-8", errors="replace")
            return {"path": path, "content": text, "truncated": truncated,
                    "source": "local", "error": ""}
        except Exception as ex:
            return {"path": path, "content": "", "source": "local",
                    "error": f"read failed: {ex}"}
    args = ["cat"]
    if revision is not None:
        args += ["-r", str(revision)]
    args.append(path)
    out, err = run_svn(args)
    truncated = len(out) > max_bytes
    return {"path": path, "content": out[:max_bytes], "truncated": truncated,
            "source": "svn", "error": err}


@mcp.tool()
def svn_grep(pattern: str, path: Optional[str] = None, regex: bool = True,
             ignore_case: bool = False, max_results: int = 300,
             context: int = 0, extensions: Optional[List[str]] = None) -> dict:
    """ローカル作業コピー配下のテキストファイルを再帰検索し、マッチ箇所(file:line)を返す。

    path 省略時は SVN_WORKING_DIRECTORY 配下を検索。日本語を含むファイル/内容にも対応。
    extensions を指定すると対象拡張子を上書き（例: [".java", ".properties"]）。
    """
    base = path or SVN_WORKDIR
    if not os.path.exists(base):
        return {"matches": [], "error": f"path not found: {base}"}
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as ex:
        return {"matches": [], "error": f"invalid regex: {ex}"}
    exts = set(e.lower() for e in extensions) if extensions else _TEXT_EXTS
    matches: List[dict] = []
    files_scanned = 0
    truncated = False
    for root, dirs, files in os.walk(base):
        if ".svn" in dirs:
            dirs.remove(".svn")
        for fn in files:
            if exts and os.path.splitext(fn)[1].lower() not in exts:
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            files_scanned += 1
            for i, line in enumerate(lines):
                if rx.search(line):
                    item = {"file": fp, "line": i + 1, "text": line.rstrip("\n")[:400]}
                    if context > 0:
                        lo = max(0, i - context)
                        hi = min(len(lines), i + context + 1)
                        item["context"] = "".join(lines[lo:hi])[:1500]
                    matches.append(item)
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    return {"matches": matches, "count": len(matches),
            "files_scanned": files_scanned, "truncated": truncated, "error": ""}


# ---------------------------------------------------------------------------
# 開発規模計測ツール
# ---------------------------------------------------------------------------
_COMMENT_MARKERS = {
    ".java": ("//", "/*", "*", "*/"),
    ".js":   ("//", "/*", "*", "*/"),
    ".ts":   ("//", "/*", "*", "*/"),
    ".vue":  ("//", "/*", "*", "*/"),
    ".py":   ("#",),
    ".sql":  ("--", "/*", "*", "*/"),
    ".css":  ("/*", "*", "*/"),
    ".xml":  ("<!--", "-->", "<!--"),
    ".html": ("<!--", "-->"),
    ".htm":  ("<!--", "-->"),
    ".properties": ("#",),
    ".yml":  ("#",),
    ".yaml": ("#",),
}


def _classify_line(line: str, ext: str) -> str:
    """行を 'code' / 'comment' / 'blank' に分類する（簡易版）。"""
    s = line.strip()
    if not s:
        return "blank"
    markers = _COMMENT_MARKERS.get(ext, ())
    for m in markers:
        if s.startswith(m):
            return "comment"
    return "code"


@mcp.tool()
def svn_loc_stats(path: Optional[str] = None,
                  extensions: Optional[List[str]] = None) -> dict:
    """ローカル作業コピー配下のテキストファイルの行数統計を返す。

    拡張子ごとに total/code/comment/blank を集計する。
    extensions を指定すると対象拡張子を絞り込める（例: [".java", ".xml"]）。
    """
    base = path or SVN_WORKDIR
    if not os.path.exists(base):
        return {"by_extension": {}, "total": {}, "error": f"path not found: {base}"}
    exts = set(e.lower() for e in extensions) if extensions else _TEXT_EXTS
    by_ext: dict = {}
    files_scanned = 0
    for root, dirs, files in os.walk(base):
        if ".svn" in dirs:
            dirs.remove(".svn")
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in exts:
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            files_scanned += 1
            bucket = by_ext.setdefault(ext, {"files": 0, "total": 0, "code": 0, "comment": 0, "blank": 0})
            bucket["files"] += 1
            bucket["total"] += len(lines)
            for line in lines:
                kind = _classify_line(line, ext)
                bucket[kind] += 1
    total = {"files": 0, "total": 0, "code": 0, "comment": 0, "blank": 0}
    for v in by_ext.values():
        for k in total:
            total[k] += v[k]
    return {
        "by_extension": dict(sorted(by_ext.items(), key=lambda x: -x[1]["total"])),
        "total": total,
        "files_scanned": files_scanned,
        "error": "",
    }


@mcp.tool()
def svn_commit_stats(repo_path: Optional[str] = None,
                     limit: Optional[int] = None,
                     revision: Optional[str] = None,
                     group_by_author: bool = True,
                     group_by_month: bool = False) -> dict:
    """SVN ログからコミット活動統計を集計する。

    著者別コミット数・月別コミット数・変更ファイル数などを返す。
    limit/revision は svn_log と同様に指定できる。
    """
    args = ["log", "--xml", "--verbose"]
    if limit:
        args += ["-l", str(int(limit))]
    if revision:
        args += ["-r", str(revision)]
    if repo_path:
        args.append(repo_path)
    out, err = run_svn(args)
    if err and not out:
        return {"error": err}
    root, perr = _parse_xml(out)
    if root is None:
        return {"error": perr or err}

    by_author: dict = {}
    by_month: dict = {}
    total_commits = 0
    total_changed_paths = 0

    for e in root.findall("logentry"):
        total_commits += 1
        author = e.findtext("author") or "(unknown)"
        date_str = e.findtext("date") or ""
        month = date_str[:7]  # "2024-03"
        plist = e.find("paths")
        changed = len(plist.findall("path")) if plist is not None else 0
        total_changed_paths += changed

        if group_by_author:
            bucket = by_author.setdefault(author, {"commits": 0, "changed_paths": 0})
            bucket["commits"] += 1
            bucket["changed_paths"] += changed

        if group_by_month and month:
            mbucket = by_month.setdefault(month, {"commits": 0, "changed_paths": 0})
            mbucket["commits"] += 1
            mbucket["changed_paths"] += changed

    result: dict = {
        "total_commits": total_commits,
        "total_changed_paths": total_changed_paths,
        "error": err,
    }
    if group_by_author:
        result["by_author"] = dict(sorted(by_author.items(), key=lambda x: -x[1]["commits"]))
    if group_by_month:
        result["by_month"] = dict(sorted(by_month.items()))
    return result


@mcp.tool()
def svn_size_stats(path: Optional[str] = None,
                   extensions: Optional[List[str]] = None,
                   top_n: int = 20) -> dict:
    """ローカル作業コピー配下のファイル規模を拡張子別に集計する。

    ファイル数・合計バイト数・最大ファイル上位 top_n 件を返す。
    extensions を指定すると対象拡張子を絞り込める。
    """
    base = path or SVN_WORKDIR
    if not os.path.exists(base):
        return {"by_extension": {}, "total": {}, "error": f"path not found: {base}"}
    exts = set(e.lower() for e in extensions) if extensions else None
    by_ext: dict = {}
    large_files: list = []
    files_scanned = 0
    for root, dirs, files in os.walk(base):
        if ".svn" in dirs:
            dirs.remove(".svn")
        for fn in files:
            ext = os.path.splitext(fn)[1].lower() or "(no ext)"
            if exts and ext not in exts:
                continue
            fp = os.path.join(root, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            files_scanned += 1
            bucket = by_ext.setdefault(ext, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += size
            large_files.append({"path": fp, "bytes": size, "ext": ext})

    total_bytes = sum(v["bytes"] for v in by_ext.values())
    total_files = sum(v["files"] for v in by_ext.values())
    large_files.sort(key=lambda x: -x["bytes"])

    return {
        "by_extension": dict(sorted(by_ext.items(), key=lambda x: -x[1]["bytes"])),
        "total": {"files": total_files, "bytes": total_bytes,
                  "mb": round(total_bytes / 1048576, 2)},
        "top_largest_files": large_files[:top_n],
        "files_scanned": files_scanned,
        "error": "",
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
