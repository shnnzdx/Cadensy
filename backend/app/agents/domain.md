# Domain Docs

How engineering-oriented agent skills should consume this repo's domain documentation.

## Before Exploring, Read These

- `README.md` at the repo root for workspace structure and local run instructions.
- `CONTEXT.md` at the repo root when it exists.
- `application_docs/adr/` for architectural decisions when it exists.

If `CONTEXT.md` or `application_docs/adr/` do not exist yet, proceed silently and continue the task.

## File Structure

This repo currently follows a single-context layout:

```text
/
|-- AI.md
|-- README.md
|-- CONTEXT.md
|-- application_docs/
|   |-- adr/
|   `-- agents/
|-- frontend/
`-- trip/
```

## Use Shared Vocabulary

When naming features, flows, or concepts, prefer the language already used in this repo:

- `frontend` for the main product site
- `trip` or `TripSync` for the workspace app
- `frontend/public/trip-app/` for the embedded built static app

If a needed term is missing, prefer adding it to `CONTEXT.md` once the team agrees on it.

## Flag ADR Conflicts

If a proposed change contradicts an ADR, call that out explicitly instead of silently overriding it.
