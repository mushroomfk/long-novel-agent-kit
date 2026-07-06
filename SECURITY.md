# Security

Long Novel Agent Kit is designed for local desktop use. It stores novel state in the local project directory and does not require a hosted service.

## Sensitive Data

Novel manuscripts, source summaries, research notes, and verification evidence can contain private story material. Do not publish a `.novel-agent/` folder or desktop evidence files unless you have reviewed and sanitized them.

Generated desktop packs may include local paths. The top-level `START_HERE` launcher refreshes moved bundles, but users should still review files before sharing a pack publicly.

## Reporting Security Issues

Please do not open a public issue for a vulnerability that exposes private data. Use a private GitHub security advisory when available, or contact the repository owner privately.

## Boundaries

- The kit does not upload manuscripts.
- The kit does not call LLM APIs by itself.
- The kit does not include web search, PDF/OCR parsing, or embedding retrieval.
- Desktop agents that use the kit may have their own network behavior. Review the host agent before giving it private manuscripts.
