# Contributing

Thanks for improving Long Novel Agent Kit.

## Development Setup

Use Python 3.10 or newer. The core project intentionally uses the Python standard library.

Run the basic checks:

```bash
python -m py_compile cli.py server.py install.py scripts/verify_agent_kit.py scripts/adversarial_release_check.py
python scripts/verify_agent_kit.py
python scripts/adversarial_release_check.py
```

## Contribution Rules

- Keep the kit local-first. Do not add network calls to manuscript workflows.
- Keep MCP read-only mode safe. Writer tools must remain hidden or rejected in read-only mode.
- Keep `.novel-agent/` as the only durable novel-state folder.
- Require author confirmation for durable writer operations.
- Add adversarial examples for new continuity checks.
- Update `README.md`, `README.zh-CN.md`, and `docs/` when behavior changes.

## Pull Request Checklist

- Explain the user-facing behavior change.
- Include verification commands and results.
- Mention any unverified platform, especially Windows runtime builds.
- Do not commit generated runtime binaries, `.novel-agent/` project state, local MCP config files, or private manuscripts.
