"""Pure offline contract for a reviewed ECS zero-to-one recovery release.

This module deliberately has no AWS SDK, subprocess, or network dependency.
The release workflow supplies dispatch inputs and read-only AWS JSON responses.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
from typing import Any

_PREPARE_PATH = Path(__file__).with_name("prepare_ecs_task_definition.py")
_prepare_spec = importlib.util.spec_from_file_location("cd01_prepare_task_definition", _PREPARE_PATH)
assert _prepare_spec and _prepare_spec.loader
_prepare = importlib.util.module_from_spec(_prepare_spec)
_prepare_spec.loader.exec_module(_prepare)
APPROVED_IMAGE_RE = _prepare.APPROVED_IMAGE_RE
CONTAINER_NAME = _prepare.CONTAINER_NAME
DEFAULT_OFF_GATES = _prepare.DEFAULT_OFF_GATES

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_TASK_DEFINITION_ARN = re.compile(
    r"^arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:[1-9][0-9]*$"
)


class CapacityRecoveryContractError(ValueError):
    """Raised when a recovery release cannot prove its offline preconditions."""


@dataclass(frozen=True)
class RecoveryRequest:
    release_mode: str
    expected_current_task_definition: str
    expected_current_image_digest: str
    expected_current_desired_count: int
    release_desired_count: int
    rollback_evidence_sha: str | None


def _require_string(inputs: dict[str, str], name: str) -> str:
    value = inputs.get(name)
    if not isinstance(value, str):
        raise CapacityRecoveryContractError(f"{name} must be a string")
    return value


def _environment_map(container: dict[str, Any]) -> dict[str, str]:
    entries = container.get("environment")
    if not isinstance(entries, list):
        raise CapacityRecoveryContractError("application environment must be a list")
    values: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise CapacityRecoveryContractError("application environment contains a non-object")
        name, value = entry.get("name"), entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in values:
            raise CapacityRecoveryContractError("application environment is malformed")
        values[name] = value
    return values


def validate_recovery_inputs(inputs: dict[str, str]) -> RecoveryRequest:
    """Validate explicit, non-arbitrary recovery dispatch inputs."""

    mode = _require_string(inputs, "release_mode")
    task_definition = _require_string(inputs, "expected_current_task_definition")
    image_digest = _require_string(inputs, "expected_current_image_digest")
    expected_count = _require_string(inputs, "expected_current_desired_count")
    recovery_count = _require_string(inputs, "recovery_desired_count")
    rollback_evidence = _require_string(inputs, "rollback_evidence_sha")

    if not _TASK_DEFINITION_ARN.fullmatch(task_definition):
        raise CapacityRecoveryContractError("expected_current_task_definition is not approved")
    if not _SHA256.fullmatch(image_digest):
        raise CapacityRecoveryContractError("expected_current_image_digest is not a sha256 digest")
    if not expected_count.isdecimal():
        raise CapacityRecoveryContractError("expected_current_desired_count must be a non-negative integer")

    if mode == "standard":
        if int(expected_count) < 1:
            raise CapacityRecoveryContractError("standard releases require a nonzero baseline")
        if recovery_count or rollback_evidence:
            raise CapacityRecoveryContractError("standard releases cannot carry recovery inputs")
        return RecoveryRequest(
            release_mode=mode,
            expected_current_task_definition=task_definition,
            expected_current_image_digest=image_digest,
            expected_current_desired_count=int(expected_count),
            release_desired_count=int(expected_count),
            rollback_evidence_sha=None,
        )

    if mode != "zero_capacity_recovery":
        raise CapacityRecoveryContractError("release_mode must be standard or zero_capacity_recovery")
    if expected_count != "0":
        raise CapacityRecoveryContractError("zero-capacity recovery requires an expected count of 0")
    if recovery_count != "1":
        raise CapacityRecoveryContractError("zero-capacity recovery requires an explicit count of 1")
    if not _GIT_SHA.fullmatch(rollback_evidence):
        raise CapacityRecoveryContractError("zero-capacity recovery requires rollback evidence SHA")

    return RecoveryRequest(
        release_mode=mode,
        expected_current_task_definition=task_definition,
        expected_current_image_digest=image_digest,
        expected_current_desired_count=0,
        release_desired_count=1,
        rollback_evidence_sha=rollback_evidence,
    )


def validate_live_baseline(
    service_payload: dict[str, Any],
    task_definition_payload: dict[str, Any],
    request: RecoveryRequest,
) -> None:
    """Fail closed if a read-only ECS baseline differs from approved inputs."""

    services = service_payload.get("services")
    if not isinstance(services, list) or len(services) != 1:
        raise CapacityRecoveryContractError("expected exactly one active service")
    service = services[0]
    if service.get("status") != "ACTIVE":
        raise CapacityRecoveryContractError("service must be active")
    if service.get("taskDefinition") != request.expected_current_task_definition:
        raise CapacityRecoveryContractError("current task definition drifted")
    for name in ("desiredCount", "runningCount", "pendingCount"):
        if not isinstance(service.get(name), int):
            raise CapacityRecoveryContractError(f"{name} must be an integer")
    if service["desiredCount"] != request.expected_current_desired_count:
        raise CapacityRecoveryContractError("desired count drifted")
    if service["pendingCount"] != 0:
        raise CapacityRecoveryContractError("pending tasks prevent promotion")
    if request.release_mode == "zero_capacity_recovery":
        if service["runningCount"] != 0:
            raise CapacityRecoveryContractError("zero-capacity baseline has running tasks")
    elif service["runningCount"] != request.expected_current_desired_count:
        raise CapacityRecoveryContractError("standard baseline is not steady")

    deployments = service.get("deployments")
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise CapacityRecoveryContractError("baseline must have one completed deployment")
    deployment = deployments[0]
    if (
        deployment.get("status") != "PRIMARY"
        or deployment.get("rolloutState") != "COMPLETED"
        or deployment.get("taskDefinition") != request.expected_current_task_definition
    ):
        raise CapacityRecoveryContractError("baseline deployment drifted")

    configuration = service.get("deploymentConfiguration")
    breaker = configuration.get("deploymentCircuitBreaker") if isinstance(configuration, dict) else None
    if (
        not isinstance(configuration, dict)
        or configuration.get("minimumHealthyPercent") != 100
        or configuration.get("maximumPercent") != 200
        or not isinstance(breaker, dict)
        or breaker.get("enable") is not True
        or breaker.get("rollback") is not True
    ):
        raise CapacityRecoveryContractError("deployment safety configuration drifted")

    task_definition = task_definition_payload.get("taskDefinition", task_definition_payload)
    if not isinstance(task_definition, dict):
        raise CapacityRecoveryContractError("task definition payload is malformed")
    containers = task_definition.get("containerDefinitions")
    matches = [
        container for container in containers if isinstance(container, dict) and container.get("name") == CONTAINER_NAME
    ] if isinstance(containers, list) else []
    if len(matches) != 1:
        raise CapacityRecoveryContractError("expected exactly one application container")
    container = matches[0]
    image = container.get("image")
    if not isinstance(image, str) or not APPROVED_IMAGE_RE.fullmatch(image):
        raise CapacityRecoveryContractError("current image is not an approved digest-pinned backend image")
    if not image.endswith("@" + request.expected_current_image_digest):
        raise CapacityRecoveryContractError("current image digest drifted")

    environment = _environment_map(container)
    if environment.get("APP_ENV") != "production":
        raise CapacityRecoveryContractError("application is not in production mode")
    if environment.get("DISABLE_SCHEDULER") != "1":
        raise CapacityRecoveryContractError("scheduler must remain disabled")
    if environment.get("DEV_ALLOW_MEMBERSHIP_HEADER") != "0":
        raise CapacityRecoveryContractError("development membership header must be disabled")
    for gate in DEFAULT_OFF_GATES:
        if environment.get(gate) != "0":
            raise CapacityRecoveryContractError(f"protected gate {gate} is not explicitly off")

    log_configuration = container.get("logConfiguration")
    if not isinstance(log_configuration, dict) or log_configuration.get("logDriver") != "awslogs":
        raise CapacityRecoveryContractError("application logging configuration drifted")
    secrets = container.get("secrets")
    if not isinstance(secrets, list) or not any(
        isinstance(secret, dict) and secret.get("name") == "DATABASE_URL" and secret.get("valueFrom")
        for secret in secrets
    ):
        raise CapacityRecoveryContractError("database secret reference is missing")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CapacityRecoveryContractError(f"cannot read JSON evidence from {path}") from error
    if not isinstance(payload, dict):
        raise CapacityRecoveryContractError(f"JSON evidence at {path} must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    """Validate workflow inputs and optional read-only ECS evidence, then print count."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-mode", required=True)
    parser.add_argument("--expected-current-task-definition", required=True)
    parser.add_argument("--expected-current-image-digest", required=True)
    parser.add_argument("--expected-current-desired-count", required=True)
    parser.add_argument("--recovery-desired-count", default="")
    parser.add_argument("--rollback-evidence-sha", default="")
    parser.add_argument("--service-json", type=Path)
    parser.add_argument("--task-definition-json", type=Path)
    args = parser.parse_args(argv)

    try:
        request = validate_recovery_inputs(
            {
                "release_mode": args.release_mode,
                "expected_current_task_definition": args.expected_current_task_definition,
                "expected_current_image_digest": args.expected_current_image_digest,
                "expected_current_desired_count": args.expected_current_desired_count,
                "recovery_desired_count": args.recovery_desired_count,
                "rollback_evidence_sha": args.rollback_evidence_sha,
            }
        )
        if bool(args.service_json) != bool(args.task_definition_json):
            raise CapacityRecoveryContractError(
                "service-json and task-definition-json must be supplied together"
            )
        if args.service_json and args.task_definition_json:
            validate_live_baseline(
                _read_json(args.service_json), _read_json(args.task_definition_json), request
            )
    except CapacityRecoveryContractError as error:
        parser.error(str(error))

    print(request.release_desired_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
