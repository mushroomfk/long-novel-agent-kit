# Changelog

## 2026-07-06

### Initial Open Source Release

- Published Long Novel Agent Kit as a standalone local desktop-agent continuity toolkit.
- Included CLI, stdio MCP server, skill instructions, installer, schemas, review panel, examples, and regression tests.
- Added no-Python handoff bundle support through `standalone-build` and `desktop-handoff-bundle`.
- Added release-grade adversarial verification through `scripts/adversarial_release_check.py`.

Validation:

- `python -m py_compile cli.py server.py install.py scripts/verify_agent_kit.py scripts/adversarial_release_check.py`
- `python scripts/verify_agent_kit.py`
- `python scripts/adversarial_release_check.py`
