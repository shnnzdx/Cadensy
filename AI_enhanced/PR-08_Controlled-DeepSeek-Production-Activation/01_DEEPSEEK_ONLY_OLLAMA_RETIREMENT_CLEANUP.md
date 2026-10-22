# CD-02 DeepSeek-only Ollama Retirement Cleanup

## Trigger and decision

The authorized CD-02 activation run `39817949726` reached all approval,
Release-A baseline, image-provenance, Secret-metadata, and IAM prechecks. It
stopped before task-definition registration because the current Release A task
definition retained legacy Ollama environment configuration. No ECS update,
Secret read, Provider request, migration, or rollback was needed.

The product decision is now explicit: production AI is DeepSeek-only. Ollama
Cloud, local Ollama, and fallback routing are retired rather than preserved as
an alternate activation path.

## Change boundary

- The CD-02 helper removes the complete `OLLAMA` namespace and retired
  `*_AI_PROVIDER` selectors only from the **new generated activation profile**.
- The original Release A task definition remains immutable and continues to be
  the fixed Runtime-off rollback baseline until a separately approved
  DeepSeek-only Runtime-off baseline exists.
- Any non-DeepSeek provider setting that survives generation remains
  fail-closed. Provider blacklist and negative tests remain safeguards.
- The two obsolete Ollama-era configuration/provisioning workflows and their
  old manual runbooks are removed. CD-02 is the sole supported production AI
  activation entry point.

## Evidence and next boundary

Offline CD-02/CD-01 contracts, helper compilation, YAML parsing, frontend
tests, diff hygiene, and a changed-file Secret Scan are required before merge.
The AWS plan records only metadata names/types and a future, separately
authorized retirement order; it does not delete a parameter, change IAM, or
deregister a task definition.

After this cleanup merges, a new CD-02 activation requires a fresh exact
tooling SHA and separate production authorization. A bounded real-provider
production chat validation remains a later authorization.
