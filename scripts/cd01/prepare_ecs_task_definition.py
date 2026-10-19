"""Create a production-safe ECS task-definition revision from the live one.

The CD workflow deliberately starts with ``describe-task-definition`` instead
of maintaining a second hand-written copy of the production configuration.
This helper accepts only an immutable image digest and changes only the image
and the explicitly default-off safety posture.  It never reads secret values.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


APPROVED_IMAGE_RE = re.compile(
    r"^448678332746\.dkr\.ecr\.us-east-1\.amazonaws\.com/"
    r"tripsync-backend@sha256:[0-9a-f]{64}$"
)
CONTAINER_NAME = "tripsync-backend"
DEFAULT_OFF_GATES = (
    "CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES",
    "CADENSY_B2C_PROTECTED_BOOKING_WRITES",
    "CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES",
    "CADENSY_B2C_PROTECTED_VOTE_WRITES",
    "CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES",
    "CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES",
    "CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES",
    "CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES",
    "CADENSY_B2C_PROTECTED_CONSTRAINT_UPDATE_WRITES",
    "CADENSY_B2C_PROTECTED_CONSTRAINT_DELETE_WRITES",
    "CADENSY_B2C_PROTECTED_PREFERENCES_WRITES",
    "CADENSY_B2D_PROTECTED_GENERATOR_WRITES",
    "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES",
    "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES",
    "CADENSY_B2E_PROTECTED_ORGANIZER_WRITES",
    "CADENSY_B3_PROTECTED_PREVIEW_APPLY",
    # PR-06B durable conversations remain storage-only in Round 1. The
    # deployment helper makes this a task-definition invariant rather than
    # relying on the application's safe default.
    "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED",
    # This exists only for the disposable browser harness. Pin it off before
    # process startup as defence in depth.
    "CADENSY_E2E_FAKE_RUNTIME",
)

# The Round 1 release profile must never receive a credential that could make a
# legacy agent route reach a paid model provider.  These are credentials, not
# harmless model-selection settings.  Keep the list explicit so non-AI service
# credentials (for example GEOAPIFY_API_KEY) remain intact.
AI_PROVIDER_CREDENTIAL_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_BEDROCK_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "COHERE_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "HUGGINGFACE_API_KEY",
        "MISTRAL_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_CLOUD_API_KEY",
        "OPENAI_API_KEY",
        "PERPLEXITY_API_KEY",
        "TOGETHER_API_KEY",
        "XAI_API_KEY",
    }
)

# These non-model API credentials are present in the reviewed production task
# definition.  A newly introduced *_API_KEY is rejected rather than copied into
# a Provider-off release by accident; its owner must classify it explicitly.
APPROVED_NON_AI_API_KEY_NAMES = frozenset({"GEOAPIFY_API_KEY"})

# Fields returned by describe-task-definition that are accepted by
# register-task-definition.  Deliberately excluding status/revision/ARN and
# timestamps prevents accidental replay of server-owned metadata.
REGISTRABLE_FIELDS = (
    "family",
    "taskRoleArn",
    "executionRoleArn",
    "networkMode",
    "containerDefinitions",
    "volumes",
    "placementConstraints",
    "requiresCompatibilities",
    "cpu",
    "memory",
    "pidMode",
    "ipcMode",
    "proxyConfiguration",
    "inferenceAccelerators",
    "ephemeralStorage",
    "runtimePlatform",
)


class ContractError(ValueError):
    """The live task definition cannot safely be promoted by CD-01."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _task_definition(payload: dict[str, Any]) -> dict[str, Any]:
    task_definition = payload.get("taskDefinition", payload)
    if not isinstance(task_definition, dict):
        _fail("Task-definition input must be a JSON object.")
    return task_definition


def _environment_map(container: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    values: dict[str, str] = {}
    order: list[str] = []
    environment = container.get("environment", [])
    if not isinstance(environment, list):
        _fail("Container environment must be a list.")
    for entry in environment:
        if not isinstance(entry, dict):
            _fail("Container environment contains a non-object entry.")
        name, value = entry.get("name"), entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            _fail("Container environment entries must have string name and value.")
        if name in values:
            _fail(f"Duplicate environment variable {name!r}.")
        values[name] = value
        order.append(name)
    return order, values


def _is_provider_credential(name: object) -> bool:
    return isinstance(name, str) and name.upper() in AI_PROVIDER_CREDENTIAL_NAMES


def _remove_provider_credentials(container: dict[str, Any]) -> None:
    """Remove provider credentials while retaining every non-AI secret.

    The live task definition is deliberately cloned for configuration
    preservation.  Removing known model-provider credentials here prevents an
    older task definition from silently re-enabling paid Provider execution
    during Gate M or Gate R.
    """
    secrets = container.get("secrets", [])
    if not isinstance(secrets, list):
        _fail("Container secrets must be a list.")
    for entry in secrets:
        if not isinstance(entry, dict):
            _fail("Container secrets contains a non-object entry.")
        name = entry.get("name")
        if (
            isinstance(name, str)
            and name.upper().endswith("_API_KEY")
            and name.upper() not in AI_PROVIDER_CREDENTIAL_NAMES
            and name.upper() not in APPROVED_NON_AI_API_KEY_NAMES
        ):
            _fail(
                f"Unclassified API-key secret {name!r} cannot enter the Provider-off release profile."
            )
    container["secrets"] = [
        entry for entry in secrets if not _is_provider_credential(entry.get("name"))
    ]


def _validate_live_contract(task_definition: dict[str, Any], container: dict[str, Any]) -> None:
    if task_definition.get("networkMode") != "awsvpc":
        _fail("Production Fargate task definition must use awsvpc networking.")
    if "FARGATE" not in task_definition.get("requiresCompatibilities", []):
        _fail("Production task definition is not Fargate-compatible.")
    if not task_definition.get("executionRoleArn") or not task_definition.get("taskRoleArn"):
        _fail("Both ECS execution and task roles must be preserved.")
    if not task_definition.get("cpu") or not task_definition.get("memory"):
        _fail("Task CPU and memory must be present.")
    if container.get("logConfiguration", {}).get("logDriver") != "awslogs":
        _fail("The application container must retain its awslogs configuration.")
    secrets = container.get("secrets", [])
    if not isinstance(secrets, list) or not any(
        entry.get("name") == "DATABASE_URL" and entry.get("valueFrom") for entry in secrets
    ):
        _fail("The application container must retain a DATABASE_URL secret reference.")
    if not any(
        entry.get("name") == "CONVERSATION_SUBMISSION_HMAC_KEY" and entry.get("valueFrom")
        for entry in secrets
    ):
        _fail(
            "The application container must retain a CONVERSATION_SUBMISSION_HMAC_KEY "
            "secret reference before a PR-06B release."
        )
    _, environment = _environment_map(container)
    if "DATABASE_URL" in environment:
        _fail("DATABASE_URL must be an ECS secret reference, not plaintext environment data.")
    if environment.get("APP_ENV") != "production":
        _fail("CD-01 will only promote a task definition already marked APP_ENV=production.")
    if environment.get("DISABLE_SCHEDULER") != "1":
        _fail("CD-01 requires the scheduler to remain disabled for this release profile.")


def prepare_task_definition(payload: dict[str, Any], image: str) -> dict[str, Any]:
    """Return a registrable task definition with a digest-pinned application image."""
    if not APPROVED_IMAGE_RE.fullmatch(image):
        _fail("Image must be the approved backend ECR repository pinned by sha256 digest.")

    task_definition = _task_definition(payload)
    containers = task_definition.get("containerDefinitions")
    if not isinstance(containers, list):
        _fail("Task definition is missing containerDefinitions.")
    matches = [container for container in containers if container.get("name") == CONTAINER_NAME]
    if len(matches) != 1:
        _fail(f"Expected exactly one {CONTAINER_NAME!r} container.")
    container = matches[0]
    _validate_live_contract(task_definition, container)

    prepared = {
        field: copy.deepcopy(task_definition[field])
        for field in REGISTRABLE_FIELDS
        if field in task_definition
    }
    target = next(
        candidate
        for candidate in prepared["containerDefinitions"]
        if candidate.get("name") == CONTAINER_NAME
    )
    target["image"] = image
    order, environment = _environment_map(target)
    for name in tuple(environment):
        if _is_provider_credential(name):
            del environment[name]
    _remove_provider_credentials(target)
    environment["DEV_ALLOW_MEMBERSHIP_HEADER"] = "0"
    environment["MOCK_AI"] = "0"
    for gate in DEFAULT_OFF_GATES:
        environment[gate] = "0"
    ordered_names = order + [name for name in environment if name not in order]
    target["environment"] = [
        {"name": name, "value": environment[name]} for name in ordered_names
    ]
    return prepared


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        prepared = prepare_task_definition(payload, args.image)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"CD-01 task-definition contract failed: {exc}") from exc
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
