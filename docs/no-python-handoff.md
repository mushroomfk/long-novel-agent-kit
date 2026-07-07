# No-Python Handoff Bundles

A no-Python handoff bundle is a copyable folder for another computer. It includes:

- `project/`: the novel project snapshot
- `pack/`: a desktop-agent guide and machine-readable status files
- `runtime/`: standalone CLI and MCP executables
- `START_HERE.*`: launchers for macOS, Linux, PowerShell, and Command Prompt
- `agent-read-me-first.md`: short instructions for the desktop agent
- `mcp-configs/current/`: refreshed MCP snippets after launch

## Why Launchers Matter

Generated packs contain paths. When a bundle is moved to another computer, paths must be refreshed. The top-level `START_HERE` launcher does that with the bundled CLI.

After unzip:

```bash
bash START_HERE.sh
```

On macOS, `START_HERE.command` runs the same script. On Windows, use `START_HERE.cmd` or `START_HERE.ps1`.

## Build Runtime

Build on the same OS family as the target computer.

macOS:

```bash
python cli.py standalone-build --output-dir release/runtime-macos --target-os macos --apply --force --format json
```

Windows:

```powershell
py -3.12 cli.py standalone-build `
  --output-dir release\runtime-windows `
  --target-os windows `
  --apply `
  --force `
  --format json
```

For a publishable Windows `.exe` package and GitHub Actions workflow template, see [Windows Runtime Release](windows-release.md).

## Create Bundle

```bash
release/runtime-macos/long-novel-agent desktop-handoff-bundle ./my-novel \
  --platform codex \
  --mode read-only \
  --chapter 1 \
  --runtime-dir release/runtime-macos \
  --output-dir release/my-novel-agent-bundle \
  --archive \
  --force \
  --format json
```

## Safety

- Start with read-only mode.
- Do not enable writer mode until `pack/writer-mode-approval.md` is reviewed.
- Do not share a bundle publicly if `project/` contains private manuscript text.
- If `runtime/MISSING_RUNTIME.md` exists, the bundle is not yet no-Python.
