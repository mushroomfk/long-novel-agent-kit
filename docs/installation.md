# Installation

Long Novel Agent Kit supports three local usage styles.

## 1. Use From Source

This is the simplest developer path.

```bash
git clone https://github.com/mushroomfk/long-novel-agent-kit.git
cd long-novel-agent-kit
python cli.py doctor
```

Initialize a novel project:

```bash
python cli.py init ./my-novel --title "My Novel"
```

Start the read-only MCP server:

```bash
python server.py --read-only --tool-profile core
```

## 2. Install Skill and MCP Config

Print a config snippet:

```bash
python install.py --mcp-platform codex --print-mcp-config
python install.py --mcp-platform cursor --print-mcp-config
python install.py --mcp-platform claude-desktop --print-mcp-config
```

Merge Codex or Cursor local config automatically:

```bash
python install.py --mcp-platform codex --mcp-config auto
python install.py --mcp-platform cursor --mcp-config auto
```

The installer defaults to read-only MCP. Use writer mode only after reviewing the writer-mode guide:

```bash
python install.py --mcp-platform codex --writer-mcp --print-mcp-config
```

## 3. No-Python Handoff Bundle

Use this when the target computer should not install Python.

Build runtime files on the target operating system:

```bash
python cli.py standalone-build \
  --output-dir release/long-novel-agent-runtime \
  --target-os macos \
  --apply \
  --force \
  --format json
```

Create the bundle:

```bash
release/long-novel-agent-runtime/long-novel-agent desktop-handoff-bundle ./my-novel \
  --platform codex \
  --mode read-only \
  --chapter 1 \
  --runtime-dir release/long-novel-agent-runtime \
  --output-dir release/my-novel-agent-bundle \
  --archive \
  --force \
  --format json
```

On the target computer, unzip and run the top-level launcher. It refreshes paths and writes current snippets to `mcp-configs/current/`.

For a publishable Windows `.exe` package, see [Windows Runtime Release](windows-release.md).

## Platform Notes

- macOS arm64 runtime should be built on macOS arm64.
- Windows runtime should be built on Windows and produces `.exe` files.
- Cross-building PyInstaller binaries is not guaranteed.
- The source-based CLI works on Python 3.10+ when local file permissions allow writes to the novel project.
