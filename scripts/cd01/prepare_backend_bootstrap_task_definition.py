"""Prepare the one-time PR-06B backend bootstrap task-definition revision.

This helper is intentionally offline: it does not contact AWS, create an SSM
parameter, change IAM, register a task definition, or update an ECS service.
It converts an operator-supplied live task-definition JSON into the exact
release profile after the operator has separately provisioned the dedicated
HMAC SecureString and execution-role permission.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


_RELEASE_HELPER_PATH = Path(__file__).with_name("prepare_ecs_task_definition.py")
_SPEC = importlib.util.spec_from_file_location("cd01_release_helper", _RELEASE_HELPER_PATH)
assert _SPEC and _SPEC.loader
_release_helper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_release_helper)

ContractError = _release_helper.ContractError
SSM_PARAMETER_ARN_RE = re.compile(
    r"^arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/"
    r"conversation-submission-hmac$"
)


def _task_definition(payload: dict[str, Any]) -> dict[str, Any]:
    task_definition = payload.get("taskDefinition", payload)
    if not isinstance(task_definition, dict):
        raise ContractError("Task-definition input must be a JSON object.")
    return task_definition


def prepare_bootstrap_task_definition(
    payload: dict[str, Any], image: str, hmac_parameter_arn: str
) -> dict[str, Any]:
    """Add the dedicated HMAC reference, then apply the normal release contract."""
    if not SSM_PARAMETER_ARN_RE.fullmatch(hmac_parameter_arn):
        raise ContractError(
            "Bootstrap requires the dedicated production SSM parameter ARN for "
            "CONVERSATION_SUBMISSION_HMAC_KEY."
        )

    copied = json.loads(json.dumps(payload))
    task_definition = _task_definition(copied)
    containers = task_definition.get("containerDefinitions")
    if not isinstance(containers, list):
        raise ContractError("Task definition is missing containerDefinitions.")
    matches = [
        container
        for container in containers
        if isinstance(container, dict) and container.get("name") == _release_helper.CONTAINER_NAME
    ]
    if len(matches) != 1:
        raise ContractError(
            f"Expected exactly one {_release_helper.CONTAINER_NAME!r} container."
        )
    container = matches[0]
    secrets = container.get("secrets", [])
    if not isinstance(secrets, list):
        raise ContractError("Container secrets must be a list.")
    if any(
        isinstance(entry, dict)
        and entry.get("name") == "CONVERSATION_SUBMISSION_HMAC_KEY"
        for entry in secrets
    ):
        raise ContractError(
            "Bootstrap may only be used while the live task definition lacks the HMAC reference."
        )
    secrets.append(
        {
            "name": "CONVERSATION_SUBMISSION_HMAC_KEY",
            "valueFrom": hmac_parameter_arn,
        }
    )
    container["secrets"] = secrets
    return _release_helper.prepare_task_definition(copied, image)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True, help="Current backend image pinned by sha256 digest")
    parser.add_argument("--hmac-parameter-arn", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        prepared = prepare_bootstrap_task_definition(
            payload, args.image, args.hmac_parameter_arn
        )
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"CD-01 backend bootstrap contract failed: {exc}") from exc
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
