# DeepSeek-only Ollama Retirement Plan

## Status and boundary

This is an inventory and retirement plan, not an AWS deletion procedure. It
records metadata-only observations made on 2026-10-22. No parameter value,
Secret value, credential, prompt, or user data was read.

No item below may be deleted until all of these gates are met:

1. A separately authorized CD-02 Release B activation has completed and its
   bounded real-provider acceptance has passed.
2. A separately validated DeepSeek-only Runtime-off rollback baseline exists.
   It must retain Durable History while containing no Ollama environment value
   or secret reference.
3. The ECS service and every rollback target selected for operations have been
   checked for no remaining Ollama dependency.
4. An operator has approved the exact deletion targets and IAM-policy change.

## Metadata-only inventory

| Surface | Observed legacy dependency | Current action |
| --- | --- | --- |
| SSM Parameter Store | `arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/ollama-cloud-api-key`, type `SecureString` | Retain until the gates above; never read its value for retirement. |
| Secrets Manager | No Secret name containing `ollama` was listed. | Re-check immediately before deletion approval. |
| GitHub repository/Main Environment secrets | No secret name containing `ollama` was listed. | Re-check immediately before deletion approval. |
| Current ECS service | `tripsync-backend:25` has `OLLAMA_CLOUD_MODEL` and `OLLAMA_CLOUD_BASE_URL`; it has no Ollama secret reference. | Keep as the fixed Release A rollback baseline until a DeepSeek-only Runtime-off baseline is approved. |
| Historical ECS task definitions | Revisions `12–14` and `17–19` include `OLLAMA_CLOUD_BASE_URL`, `OLLAMA_CLOUD_MODEL`, and `OLLAMA_CLOUD_API_KEY`; revisions `20–25` retain only the two environment fields. | Do not deregister during Release B. Review retention and rollback needs in a separately authorized cleanup operation. |
| ECS execution-role IAM | Inline policy `tripsync-read-runtime-parameters` grants `ssm:GetParameters` on the Ollama parameter ARN. No Ollama-named policy exists. | After all task-definition dependencies are retired, remove only that ARN from the policy in a separately reviewed IAM change. |

## Ordered retirement procedure for later authorization

1. Capture the active service task definition, selected Runtime-off rollback
   target, ECS/ALB/API health, and all remaining task-definition references.
2. Confirm the DeepSeek-only activation and its Runtime-off rollback baseline
   have no `OLLAMA` namespace configuration or secret reference.
3. Update the execution-role policy to remove only the identified Ollama SSM
   ARN; validate that Database, HMAC, DeepSeek, Geoapify, and Unsplash access
   remain intact.
4. Delete the exact Ollama SSM parameter by ARN without reading its value.
5. Remove any GitHub secret or Secrets Manager secret found by the final
   metadata inventory, if present and explicitly approved.
6. Deregister historical ECS task definitions only after confirming no rollback,
   service, scheduled task, or incident procedure selects them.
7. Re-check ECS task definitions, IAM policy resources, GitHub secret names,
   and SSM/Secrets Manager metadata; record zero supported Ollama dependencies.

## Repository state

The supported production activation entry point is
`.github/workflows/cd01-release-b-ai-activation.yml`. It builds a new task
definition from the Release A image while stripping the entire `OLLAMA` naming
space and historical provider selectors from the generated activation profile.
The historical Release A task definition is never modified by this repository
cleanup.
