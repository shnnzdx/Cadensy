# Documentation and Agent Working Rules

Status: current companion to [`../AGENTS.md`](../AGENTS.md)
Last reviewed: 2026-10-28

The root `AGENTS.md` is authoritative for repository work. These rules add documentation-specific guidance.

## Read order

1. `../AGENTS.md`
2. `../README.md`
3. `../HANDOFF.md`
4. `../INTEGRATION-ROADMAP.md`
5. `README.md`
6. The current contract for the area being changed

For AI-agent work, start at `../AI_enhanced/README.md`. For backend consolidation, also read `HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md`, `../backend/README.md`, and `../backend/LOCAL_DEV.md`.

## Documentation state model

- Current contracts use a status and review date and are linked from `README.md`.
- Point-in-time evidence preserves what was known when written. Do not rewrite old conclusions to match today.
- Superseded planning, temporary addresses/assets, and old handoffs belong in `_archive/`.
- Heavily cross-referenced evidence may be archived in place with a directory-level `README.md`.

## Required behavior

- Update a current contract in the same change that invalidates it.
- Distinguish design intent, code implementation, automated test evidence, protected deployment evidence, and live production observation.
- Prefer repository-relative links and verify them after moving files.
- Preserve the frozen navigation, technical-session, and Plan workspace boundaries in the root rules.
- Never place credentials, provider keys, bearer tokens, private user content, or unredacted production payloads in documentation.

Recorded v0.3.0 evidence shows the Pydantic runtime in production. A historical Legacy statement or a local Legacy default does not override that status.
