# Technical Investigations and Audit Evidence

Status: current index of scoped investigations
Last reviewed: 2026-11-23

This directory contains dated technical investigations: source audits, caller searches, coverage checks, debugging reports and bounded experiments. Each report should state the source/code pin, what was searched, what was found, what was not searched, and what the result does not prove.

## Records

| Record | Scope | Explicit limitation |
| --- | --- | --- |
| [DESIGN-04 Phase 1 Task 0 coverage closure](design-04-phase1-task0-coverage-note.md) | 2026-11-06 read-only re-run of trip-context HTTP caller searches at code pin 0a7f68b4aec8c91b0e19a8a09f133688b22beb86 | Not runtime proof, not an exhaustive caller inventory, and not independent QA |

## Investigation record requirements

- State the exact date and code pin (commit SHA) inspected.
- List search patterns, paths, test fixtures and excluded areas.
- Separate positive findings from negative search results.
- State residual limits, dynamic behavior and out-of-repository callers that were not checked.
- Do not call a read-only code search a runtime test, QA pass or production verification unless that evidence was actually collected.

A later investigation should add a new dated record when the scope or source pin changes; preserve prior findings as historical evidence.
