# Cadensy AI Agent Guide

Status: current compatibility pointer
Last reviewed: 2026-10-28

The authoritative AI engineering record is [`AI_enhanced/README.md`](AI_enhanced/README.md). Start there before changing runtime selection, durable conversation behavior, tool execution, provider handling, or evaluation evidence.

## Current references

- [`HANDOFF.md`](HANDOFF.md) — current system and production handoff
- [`docs/HANDOFF_PROMPT_CURRENT.md`](docs/HANDOFF_PROMPT_CURRENT.md) — current continuation prompt
- [`backend/app/agents/AI_AGENT_SUMMARY.md`](backend/app/agents/AI_AGENT_SUMMARY.md) — implementation map
- [`backend/README.md`](backend/README.md) and [`backend/LOCAL_DEV.md`](backend/LOCAL_DEV.md) — operation and local validation
- [`AI_enhanced/PR-09_Pydantic-Production-Migration/README.md`](AI_enhanced/PR-09_Pydantic-Production-Migration/README.md) — Pydantic migration evidence index

## Status boundary

Recorded production evidence for Cadensy v0.3.0 shows `CHAT_AGENT_RUNTIME=pydantic` on backend task definition `tripsync-backend:31`, with the Durable Conversation production latch enabled. The local backend environment may still default to `legacy`; that is a development default, not a production-state claim.

Historical reports under `AI_enhanced/` are point-in-time evidence. Their old statements such as “Legacy active”, “not merged”, or “not authorized” must be interpreted using the report date and the current index.

Never place provider keys, AWS credentials, bearer tokens, or unredacted conversation content in AI evidence.
