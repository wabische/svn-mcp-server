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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
