# Application Documentation and Agent Working Rules

Status: current companion to [repository AGENTS.md](../AGENTS.md)
Last reviewed: 2026-11-23

The root AGENTS.md is authoritative for repository work. These rules add application-documentation-specific guidance. AWS deployment material belongs in ../AWS/; AI-agent engineering evidence belongs in ../AI_enhanced/.

## Read order

1. ../AGENTS.md
2. ../README.md
3. ../HANDOFF.md
4. ../INTEGRATION-ROADMAP.md
5. README.md
6. The current contract for the area being changed

For AI-agent work, start at ../AI_enhanced/README.md. For backend consolidation, also read HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md, ../backend/README.md, and ../backend/LOCAL_DEV.md.

## Documentation taxonomy

- **Application root:** current product contracts, active limitations, project description, current continuation prompts and these working rules. Do not add dated task reports here.
- **decisions/:** point-in-time product, architecture, security, cost and operating decisions. Record alternatives, rationale, trade-offs, assumptions, owner decision and revisit triggers. A decision is not proof of implementation or production authorization.
- **delivery-records/:** completed task, PR, merge, QA, artifact, migration, deployment and release evidence. Keep merged, published, deployed and independently verified states distinct.
- **investigations/:** scoped audits, code searches, debugging and technical investigations. Preserve code pins, exact scope, results and residual limits; do not overstate a negative search.
- **backend/ and frontend/:** historical handoffs retained at their original paths for link stability. Their directory READMEs identify current sources of truth.
- **_archive/:** superseded plans, temporary materials and replaced handoffs; never implementation guidance.

New point-in-time records must be placed in the appropriate evidence directory and indexed from README.md. Keep each record's original conclusions intact; add a newer record when evidence or decisions change.

## Documentation state model

- Current contracts use a status and review date and are linked from README.md.
- Point-in-time evidence preserves what was known when written. Do not rewrite old conclusions to match today.
- Superseded planning, temporary addresses/assets, and old handoffs belong in _archive/.
- Heavily cross-referenced evidence may be archived in place with a directory-level README.md.

## Required behavior

- Update a current contract in the same change that makes it stale.
- Distinguish design intent, code implementation, automated test evidence, protected deployment evidence, and live production observation.
- Prefer repository-relative links and verify them after moving files.
- Preserve the frozen navigation, technical-session, and Plan workspace boundaries in the root rules.
- Never place credentials, provider keys, bearer tokens, private user content, or unredacted production payloads in documentation.

Recorded v0.3.0 evidence shows the Pydantic runtime in production. A historical Legacy statement or a local Legacy default does not override that status.
