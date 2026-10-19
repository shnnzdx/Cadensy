"""Create a digest-pinned frontend ECS task-definition revision offline."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


APPROVED_IMAGE_RE = re.compile(
    r"^448678332746\.dkr\.ecr\.us-east-1\.amazonaws\.com/"
    r"tripsync-frontend@sha256:[0-9a-f]{64}$"
)
CONTAINER_NAME = "tripsync-frontend"
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
    """The current frontend task definition cannot be safely promoted."""


def _fail(message: str) -> None:
    raise ContractError(message)


def prepare_frontend_task_definition(payload: dict[str, Any], image: str) -> dict[str, Any]:
    """Preserve the live frontend profile while replacing only its image."""
    if not APPROVED_IMAGE_RE.fullmatch(image):
        _fail("Image must be the approved frontend ECR repository pinned by sha256 digest.")
    task_definition = payload.get("taskDefinition", payload)
    if not isinstance(task_definition, dict):
        _fail("Task-definition input must be a JSON object.")
    if task_definition.get("networkMode") != "awsvpc":
        _fail("Production Fargate task definition must use awsvpc networking.")
    if "FARGATE" not in task_definition.get("requiresCompatibilities", []):
        _fail("Production task definition is not Fargate-compatible.")
    if not task_definition.get("executionRoleArn"):
        _fail("The ECS execution role must be preserved.")
    if not task_definition.get("cpu") or not task_definition.get("memory"):
        _fail("Task CPU and memory must be present.")
    containers = task_definition.get("containerDefinitions")
    if not isinstance(containers, list):
        _fail("Task definition is missing containerDefinitions.")
    matches = [container for container in containers if container.get("name") == CONTAINER_NAME]
    if len(matches) != 1:
        _fail(f"Expected exactly one {CONTAINER_NAME!r} container.")
    if matches[0].get("logConfiguration", {}).get("logDriver") != "awslogs":
        _fail("The frontend container must retain its awslogs configuration.")
    if not matches[0].get("healthCheck"):
        _fail("The frontend container must retain its existing health check.")

    prepared = {
        field: copy.deepcopy(task_definition[field])
        for field in REGISTRABLE_FIELDS
        if field in task_definition
    }
    target = next(
        container
        for container in prepared["containerDefinitions"]
        if container.get("name") == CONTAINER_NAME
    )
    target["image"] = image
    return prepared


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        prepared = prepare_frontend_task_definition(payload, args.image)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"CD-01 frontend task-definition contract failed: {exc}") from exc
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
