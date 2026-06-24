# svn-mcp-server

A single-file [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server for
**Subversion (SVN)**, built to be robust on **Windows with non-ASCII (CJK / Japanese) paths**.

It was written after two existing SVN MCP servers each fell short in a Japanese / Windows
environment: a Node implementation mangled Japanese paths (CP932 vs UTF-8), and an existing
Python implementation returned empty results and crashed on empty output. This server keeps
the good parts of both and fixes those issues.

## Why this exists

- **Unicode-safe arguments.** Commands are spawned with an **argument list and `shell=False`**,
  so on Windows the path/URL is handed to `CreateProcessW` as Unicode and Japanese characters
  are *not* corrupted.
- **Explicit UTF-8 decoding.** Output is captured as bytes and decoded as UTF-8
  (`errors="replace"`), independent of the console code page.
- **XML-parsed output.** `info` / `status` / `log` / `list` use `--xml` and are parsed with
  `ElementTree`, so results are structured and reliable (no more empty `log`).
- **Env-based auth.** `SVN_USERNAME` / `SVN_PASSWORD` are passed as `--username` / `--password`
  with `--non-interactive` (no reliance on an OS credential cache).
- **No crash on empty output.** A clean working copy returns an empty list, not an exception.

## Requirements

- Python >= 3.10
- The `svn` command-line client in `PATH` (e.g. SlikSVN, TortoiseSVN CLI, CollabNet)
- `mcp` (installed automatically; provides `mcp.server.fastmcp`)

## Install

```bash
pip install "git+https://github.com/wabische/svn-mcp-server.git"
```

To update to the latest version:

```bash
pip install --upgrade "git+https://github.com/wabische/svn-mcp-server.git"
```

For local development (editable install — changes to `svn_mcp_server.py` take effect on
Claude Desktop restart without reinstalling):

```bash
git clone https://github.com/wabische/svn-mcp-server.git
cd svn-mcp-server
pip install -e .
```

## Configure (Claude Desktop / Cowork)

Add to `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "svn": {
      "command": "svn-mcp",
      "env": {
        "SVN_WORKING_DIRECTORY": "C:\\path\\to\\working\\copy",
        "SVN_USERNAME": "your_user",
        "SVN_PASSWORD": "your_password",
        "SVN_TIMEOUT": "120000",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

## Environment variables

| Variable                | Description                                   | Default          |
| ----------------------- | --------------------------------------------- | ---------------- |
| `SVN_PATH`              | Path to the `svn` executable                  | `svn`            |
| `SVN_WORKING_DIRECTORY` | Default working directory for commands        | process cwd      |
| `SVN_USERNAME`          | Username for authentication                   | (none)           |
| `SVN_PASSWORD`          | Password for authentication                   | (none)           |
| `SVN_CONFIG_DIR`        | Passed to svn as `--config-dir`               | (none)           |
| `SVN_TRUST_SERVER_CERT` | `1`/`true` to accept self-signed certs        | off              |
| `SVN_TIMEOUT`           | Per-command timeout in milliseconds           | `120000`         |

## Tools

### Read / inspect

| Tool | Description |
| ---- | ----------- |
| `svn_version` | SVN client version |
| `svn_health_check` | Check SVN availability and working copy state |
| `svn_whoami` | Current SVN username |
| `svn_info` | Detailed info for a path or URL |
| `svn_status` | Working copy file status |
| `svn_log` | Commit history |
| `svn_log_search` | Search commits by author, message, or revision range |
| `svn_diff` | Diff between revisions |
| `svn_diff_stats` | Added/removed line counts per file and extension from a diff |
| `svn_list` | Directory listing |
| `svn_cat` | File content (local or via `svn cat`) |
| `svn_grep` | Full-text regex search across working copy |

### Fetch / update

| Tool | Description |
| ---- | ----------- |
| `svn_checkout` | Checkout a URL to a local path |
| `svn_update` | Update working copy (supports `set_depth` for sparse checkouts) |
| `svn_cleanup` | Clean up interrupted operations |

### Write

| Tool | Description |
| ---- | ----------- |
| `svn_add` | Stage files for version control |
| `svn_commit` | Commit changes (**requires human review**) |
| `svn_delete` | Delete files or URLs (**requires human review**) |
| `svn_revert` | Revert local changes |

> **Note on write operations.** `svn_commit` and repository-side `svn_delete` write to version
> control. Use them only after explicit human review/approval.

### Development scale metrics

These tools measure the size and activity of a project checked out locally.

| Tool | Description |
| ---- | ----------- |
| `svn_loc_stats` | Lines-of-code breakdown (total / code / comment / blank) by file extension |
| `svn_commit_stats` | Commit activity aggregated by author and/or calendar month |
| `svn_size_stats` | File count and byte size by extension, plus the largest-files list |

#### `svn_loc_stats`

```
svn_loc_stats(path=None, extensions=None)
```

Walks the working copy and classifies every line as **code**, **comment**, or **blank**.
Results are grouped by extension and summed into a `total` entry.

- `path` — root to scan (defaults to `SVN_WORKING_DIRECTORY`)
- `extensions` — list of extensions to include, e.g. `[".java", ".xml"]`
  (defaults to the built-in text-file set)

#### `svn_commit_stats`

```
svn_commit_stats(repo_path=None, limit=None, revision=None,
                 group_by_author=True, group_by_month=False)
```

Parses `svn log --verbose --xml` and aggregates commit counts and changed-path counts.

- `group_by_author` — include per-author breakdown (default `true`)
- `group_by_month` — include per-month breakdown (default `false`)
- `limit` / `revision` — same semantics as `svn_log`

#### `svn_diff_stats`

```
svn_diff_stats(repo_path=None, revision=None, old_revision=None, new_revision=None,
               extensions=None)
```

Runs `svn diff` and parses the unified-diff output to count added and removed lines per file.
Returns a per-file list sorted by total churn, an extension breakdown, and a `total` summary
(`files_changed`, `added`, `removed`, `delta`).

- Arguments are identical to `svn_diff`
- `extensions` — restrict counting to specific extensions, e.g. `[".java"]`

#### `svn_size_stats`

```
svn_size_stats(path=None, extensions=None, top_n=20)
```

Collects file sizes from the working copy filesystem.  Returns per-extension file count
and byte totals, the overall total in bytes and MB, and the `top_n` largest individual files.

- `.svn` metadata directories are automatically excluded.

## Credits

Independent reimplementation, inspired by and interoperable with two prior MIT-licensed projects
(no code copied): [gcorroto/mcp-svn](https://github.com/gcorroto/mcp-svn) and
[manavdesai27/mcp-server-svn](https://github.com/manavdesai27/mcp-server-svn).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
