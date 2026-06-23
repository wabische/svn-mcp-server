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
# from a clone
pip install .

# or directly from GitHub
pip install "git+https://github.com/wabische/svn-mcp-server.git"
```

This installs a console script: `svn-mcp`. You can also run the single file directly with
`python svn_mcp_server.py`.

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

If you prefer not to install the package, use the single file instead:

```json
"command": "python",
"args": ["C:\\path\\to\\svn_mcp_server.py"]
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

Read / inspect: `svn_version`, `svn_health_check`, `svn_whoami`, `svn_info`, `svn_status`,
`svn_log`, `svn_diff`, `svn_list`.

Fetch / update: `svn_checkout`, `svn_update` (supports `set_depth` for sparse checkouts),
`svn_cleanup`.

Write: `svn_add`, `svn_commit`, `svn_delete`, `svn_revert`.

> **Note on write operations.** `svn_commit` and repository-side `svn_delete` write to version
> control. Use them only after explicit human review/approval.

## Credits

Independent reimplementation, inspired by and interoperable with two prior MIT-licensed projects
(no code copied): [gcorroto/mcp-svn](https://github.com/gcorroto/mcp-svn) and
[manavdesai27/mcp-server-svn](https://github.com/manavdesai27/mcp-server-svn).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
