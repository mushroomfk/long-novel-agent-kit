# Adversarial Verification

This project treats verification as a product feature. Long novels fail through subtle continuity mistakes, so tests include deliberately bad drafts and unsafe update attempts.

## Release Gate

Run:

```bash
python scripts/adversarial_release_check.py
```

The release gate checks:

- no `__pycache__`, `.DS_Store`, `.pytest_cache`, `.novel-agent`, or generated runtime binaries are committed
- no local machine paths or temporary build paths leak into source docs or examples
- all JSON and JSONL files parse
- all JSON schema files parse
- core Python files compile
- the full regression suite passes

## Full Regression

Run:

```bash
python scripts/verify_agent_kit.py
```

The suite covers:

- mini Gaoxia project import
- chapter context generation
- chapter checks for required, forbidden, future, fact, prop, relationship, location, timeline, and life-state conflicts
- contract diffing and chapter readiness
- proposal schema, evidence, risk, review, diff, apply, reject, and rollback
- read-only MCP tool exposure and writer rejection
- MCP tool-profile filtering
- desktop setup, desktop verify, desktop checklist, desktop evidence, and verification matrix
- desktop pack generation, schemas, command indexes, local HTML entry files, pack doctor, pack freshness, and archive checks
- no-Python handoff bundle generation, missing-runtime behavior, and bundle-relative runtime commands
- long-form multi-chapter continuity chain
- 20-chapter stress case
- adversarial samples in `examples/adversarial-cases/`

## Important Adversarial Cases

- A draft transfers a prop to the wrong holder.
- A chapter reveals a future marker too early.
- A proposal claims a high-risk death or identity reveal without author review.
- A handoff proposal conflicts with existing facts.
- A draft reverses relationship or life-state facts.
- Desktop evidence uses weak proof such as "ok" or "done".
- Writer mode omits write-session preflight.
- Writer mode omits author confirmation.

## What Is Not Verified Automatically

- Literary quality.
- Real model output quality.
- Real desktop GUI behavior for every client.
- Cross-built PyInstaller executables on operating systems not used for the build.

Those checks need human review or platform-specific CI.
