# Longform Continuity Chain Example

This example verifies a six-chapter continuity chain without calling a model.

The fixed verifier copies these files to a temporary project, records chapters 1-6, applies structured post-write proposals, checks range readiness for chapters 4-6, then opens chapter 7 through `build-context` and checks a deliberately invalid draft.

It covers:

- Accepted chapter tails feeding the next chapter context.
- Post-write facts surviving across chapters.
- Open plot debts and handoff text reaching chapter 7.
- Rule-based detection for prop ownership, relationship reversal, location, life state, forbidden moves, and future markers.

Run:

```bash
python agent-kits/long-novel-agent/scripts/verify_agent_kit.py
```
