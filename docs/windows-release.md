# Windows Runtime Release

This page describes how to publish a no-Python Windows runtime for Long Novel Agent Kit.

The Windows runtime is the user-facing package for ordinary Windows users. It should contain:

- `long-novel-agent.exe`: local CLI for project setup, desktop packs, checks, and handoff bundles
- `long-novel-agent-mcp.exe`: local stdio MCP server for desktop agents
- release notes or build report
- optional starter handoff bundle generated from a sample or user project

## Important Boundary

Build Windows executables on Windows.

PyInstaller does not reliably cross-build Windows `.exe` files from macOS or Linux. A source zip, a Python wheel, or a macOS-built runtime is not a no-Python Windows release. If `runtime/` does not contain both Windows executables, ordinary Windows users still need Python.

## Manual Build On Windows

Install Python 3.12, then run from the repository root:

```powershell
py -3.12 -m pip install --upgrade pip pyinstaller

py -3.12 cli.py standalone-build `
  --target-os windows `
  --output-dir release\runtime-windows `
  --apply `
  --force `
  --format json | Tee-Object -FilePath release\windows-build-report.json
```

Check the generated executables:

```powershell
.\release\runtime-windows\long-novel-agent.exe doctor | Tee-Object -FilePath release\windows-doctor.json
py -3.12 -m json.tool release\windows-doctor.json | Out-Null

.\release\runtime-windows\long-novel-agent.exe proposal-schema > release\proposal-schema.json
py -3.12 -m json.tool release\proposal-schema.json | Out-Null

Test-Path .\release\runtime-windows\long-novel-agent.exe
Test-Path .\release\runtime-windows\long-novel-agent-mcp.exe
```

Create the zip:

```powershell
$Version = "0.7.180"
$Zip = "release\long-novel-agent-kit-windows-runtime-v$Version.zip"

Compress-Archive `
  -Path `
    release\runtime-windows\long-novel-agent.exe, `
    release\runtime-windows\long-novel-agent-mcp.exe, `
    release\windows-build-report.json, `
    release\windows-doctor.json `
  -DestinationPath $Zip `
  -Force
```

Publish it with GitHub CLI:

```powershell
gh release create v0.7.180-windows `
  release\long-novel-agent-kit-windows-runtime-v0.7.180.zip `
  --repo mushroomfk/long-novel-agent-kit `
  --title "Long Novel Agent Kit Windows Runtime v0.7.180" `
  --notes "No-Python Windows runtime. Includes long-novel-agent.exe and long-novel-agent-mcp.exe."
```

## GitHub Actions Build

Use `docs/github-actions-windows-release.yml` as the workflow template.

To enable it:

```bash
mkdir -p .github/workflows
cp docs/github-actions-windows-release.yml .github/workflows/windows-release.yml
git add .github/workflows/windows-release.yml
git commit -m "Add Windows runtime release workflow"
git push
```

The GitHub token used for that push must have `workflow` scope. Without that scope, GitHub rejects changes under `.github/workflows/`.

After the workflow is enabled, either run it manually from GitHub Actions or push a tag such as:

```bash
git tag v0.7.180-windows
git push origin v0.7.180-windows
```

## Verify A Downloaded Windows Runtime

On a Windows computer, unzip the release and run:

```powershell
.\long-novel-agent.exe doctor
.\long-novel-agent.exe proposal-schema > proposal-schema.json
py -3.12 -m json.tool proposal-schema.json | Out-Null
```

If the user should not install Python, skip the `json.tool` command and check that `doctor` prints JSON and exits successfully.

## Create A No-Python Handoff Bundle

After the Windows runtime is built, create a bundle for another Windows desktop agent:

```powershell
.\release\runtime-windows\long-novel-agent.exe desktop-handoff-bundle .\my-novel `
  --platform codex `
  --mode read-only `
  --chapter 1 `
  --runtime-dir .\release\runtime-windows `
  --output-dir .\release\my-novel-agent-bundle `
  --archive `
  --force `
  --format json
```

The target Windows computer should open `START_HERE.cmd` or `START_HERE.ps1` from the bundle root.
