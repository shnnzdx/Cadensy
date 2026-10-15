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
_MANIFEST_SCHEMA_VERSION = "pr06c-recovery-safety/v1"


class CapacityRecoveryContractError(ValueError):
    """Raised when a recovery release cannot prove its offline preconditions."""


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapacityRecoveryContractError(f"{name} must be an object")
    return value


def _require_evidence_ref(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("https://github.com/shnnzdx/Cadensy/"):
        raise CapacityRecoveryContractError(f"{name} must be a Cadensy GitHub evidence reference")
    return value


def _require_nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CapacityRecoveryContractError(f"{name} must be a non-negative integer")
    return value


def _normalized_awsvpc_configuration(value: object, name: str) -> dict[str, Any]:
    configuration = _require_mapping(value, name)
    awsvpc = _require_mapping(configuration.get("awsvpcConfiguration"), f"{name}.awsvpcConfiguration")
    assign_public_ip = awsvpc.get("assignPublicIp")
    if assign_public_ip not in {"ENABLED", "DISABLED"}:
        raise CapacityRecoveryContractError(f"{name}.assignPublicIp is invalid")

    normalized: dict[str, Any] = {"assignPublicIp": assign_public_ip}
    for field in ("securityGroups", "subnets"):
        values = awsvpc.get(field)
        if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
            raise CapacityRecoveryContractError(f"{name}.{field} must be a non-empty string list")
        if len(set(values)) != len(values):
            raise CapacityRecoveryContractError(f"{name}.{field} contains duplicates")
        normalized[field] = sorted(values)
    return {"awsvpcConfiguration": normalized}


def _normalized_load_balancers(value: object, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CapacityRecoveryContractError(f"{name} must contain at least one target group")
    normalized: list[dict[str, Any]] = []
    for load_balancer in value:
        item = _require_mapping(load_balancer, name)
        container_name = item.get("containerName")
        target_group_arn = item.get("targetGroupArn")
        container_port = item.get("containerPort")
        if (
            not isinstance(container_name, str)
            or not container_name
            or not isinstance(target_group_arn, str)
            or not target_group_arn.startswith("arn:aws:elasticloadbalancing:")
            or isinstance(container_port, bool)
            or not isinstance(container_port, int)
            or not 1 <= container_port <= 65535
        ):
            raise CapacityRecoveryContractError(f"{name} contains an invalid target-group binding")
        normalized.append(
            {
                "containerName": container_name,
                "containerPort": container_port,
                "targetGroupArn": target_group_arn,
            }
        )
    if len({item["targetGroupArn"] for item in normalized}) != len(normalized):
        raise CapacityRecoveryContractError(f"{name} contains duplicate target groups")
    return sorted(normalized, key=lambda item: item["targetGroupArn"])


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


def validate_recovery_safety_manifest(
    manifest_payload: dict[str, Any], rollback_evidence_sha: str
) -> dict[str, Any]:
    """Fail closed unless reviewed recovery evidence has positive, complete state."""

    manifest = _require_mapping(manifest_payload, "recovery safety manifest")
    if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise CapacityRecoveryContractError("recovery safety manifest schema version is not approved")
    if manifest.get("status") != "ACCEPTED":
        raise CapacityRecoveryContractError("recovery safety manifest is not accepted")

    experiment = _require_mapping(manifest.get("isolated_experiment"), "isolated_experiment")
    if experiment.get("status") != "PASSED":
        raise CapacityRecoveryContractError("isolated experiment has not passed")
    if experiment.get("environment") != "isolated-non-production":
        raise CapacityRecoveryContractError("isolated experiment environment is not approved")
    _require_evidence_ref(experiment.get("evidence_ref"), "isolated experiment evidence")
    if experiment.get("evidence_commit_sha") != rollback_evidence_sha:
        raise CapacityRecoveryContractError("isolated experiment evidence is not bound to rollback_evidence_sha")
    if experiment.get("rollback_outcome_observed") is not True:
        raise CapacityRecoveryContractError("isolated experiment did not record rollback outcome")

    compatibility = _require_mapping(
        manifest.get("old_runtime_forward_schema"), "old_runtime_forward_schema"
    )
    if compatibility.get("status") != "COMPATIBLE":
        raise CapacityRecoveryContractError("old runtime forward-schema compatibility is not accepted")
    _require_evidence_ref(compatibility.get("evidence_ref"), "old runtime schema evidence")

    approval = _require_mapping(manifest.get("approval"), "approval")
    if approval.get("status") != "APPROVED":
        raise CapacityRecoveryContractError("recovery safety approval is not approved")
    _require_evidence_ref(approval.get("evidence_ref"), "recovery safety approval evidence")

    baseline = _require_mapping(manifest.get("approved_service_baseline"), "approved_service_baseline")
    task_definition_arn = baseline.get("task_definition_arn")
    if not isinstance(task_definition_arn, str) or not _TASK_DEFINITION_ARN.fullmatch(task_definition_arn):
        raise CapacityRecoveryContractError("approved service baseline task definition is invalid")
    _normalized_awsvpc_configuration(
        baseline.get("network_configuration"), "approved_service_baseline.network_configuration"
    )
    _normalized_load_balancers(baseline.get("load_balancers"), "approved_service_baseline.load_balancers")
    if baseline.get("deployment_controller_type") != "ECS":
        raise CapacityRecoveryContractError("approved service baseline deployment controller is not ECS")
    if baseline.get("launch_type") != "FARGATE":
        raise CapacityRecoveryContractError("approved service baseline launch type is not FARGATE")
    if baseline.get("scheduling_strategy") != "REPLICA":
        raise CapacityRecoveryContractError("approved service baseline scheduling strategy is not REPLICA")
    _require_nonnegative_int(
        baseline.get("health_check_grace_period_seconds"),
        "approved_service_baseline.health_check_grace_period_seconds",
    )
    return manifest


def _validate_service_safety_shape(service: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    network_configuration = _normalized_awsvpc_configuration(
        service.get("networkConfiguration"), "service.networkConfiguration"
    )
    load_balancers = _normalized_load_balancers(service.get("loadBalancers"), "service.loadBalancers")
    deployment_controller = _require_mapping(service.get("deploymentController"), "service.deploymentController")
    if deployment_controller.get("type") != "ECS":
        raise CapacityRecoveryContractError("service deployment controller must be ECS")
    if service.get("launchType") != "FARGATE":
        raise CapacityRecoveryContractError("service launch type must be FARGATE")
    if service.get("schedulingStrategy") != "REPLICA":
        raise CapacityRecoveryContractError("service scheduling strategy must be REPLICA")
    _require_nonnegative_int(service.get("healthCheckGracePeriodSeconds"), "service health check grace period")
    return network_configuration, load_balancers


def _validate_approved_service_baseline(
    service: dict[str, Any], manifest: dict[str, Any], request: RecoveryRequest
) -> None:
    baseline = _require_mapping(manifest.get("approved_service_baseline"), "approved_service_baseline")
    if baseline.get("task_definition_arn") != request.expected_current_task_definition:
        raise CapacityRecoveryContractError("approved service baseline task definition does not match request")
    network_configuration, load_balancers = _validate_service_safety_shape(service)
    if network_configuration != _normalized_awsvpc_configuration(
        baseline.get("network_configuration"), "approved_service_baseline.network_configuration"
    ):
        raise CapacityRecoveryContractError("service network configuration drifted from approved baseline")
    if load_balancers != _normalized_load_balancers(
        baseline.get("load_balancers"), "approved_service_baseline.load_balancers"
    ):
        raise CapacityRecoveryContractError("service load-balancer configuration drifted from approved baseline")
    if service["deploymentController"]["type"] != baseline.get("deployment_controller_type"):
        raise CapacityRecoveryContractError("service deployment controller drifted from approved baseline")
    if service.get("launchType") != baseline.get("launch_type"):
        raise CapacityRecoveryContractError("service launch type drifted from approved baseline")
    if service.get("schedulingStrategy") != baseline.get("scheduling_strategy"):
        raise CapacityRecoveryContractError("service scheduling strategy drifted from approved baseline")
    if service.get("healthCheckGracePeriodSeconds") != baseline.get("health_check_grace_period_seconds"):
        raise CapacityRecoveryContractError("service health-check grace period drifted from approved baseline")


def validate_live_baseline(
    service_payload: dict[str, Any],
    task_definition_payload: dict[str, Any],
    request: RecoveryRequest,
    recovery_safety_manifest: dict[str, Any] | None = None,
) -> None:
    """Fail closed if a read-only ECS baseline differs from approved inputs."""

    services = service_payload.get("services")
    if not isinstance(services, list) or len(services) != 1:
        raise CapacityRecoveryContractError("expected exactly one active service")
    service = services[0]
    if service.get("status") != "ACTIVE":
        raise CapacityRecoveryContractError("service must be active")
    _validate_service_safety_shape(service)
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
        if recovery_safety_manifest is None:
            raise CapacityRecoveryContractError("zero-capacity recovery requires an accepted safety manifest")
        _validate_approved_service_baseline(service, recovery_safety_manifest, request)
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
    if task_definition.get("taskDefinitionArn") != request.expected_current_task_definition:
        raise CapacityRecoveryContractError("task definition payload ARN drifted")
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


def validate_deployed_runtime(
    service_payload: dict[str, Any],
    running_tasks_payload: dict[str, Any],
    target_health_payloads: list[dict[str, Any]],
    expected_task_definition_arn: str,
    expected_runtime_image_digest: str,
    expected_running_count: int,
) -> None:
    """Verify the actual deployed ECS task identity from read-only API evidence."""

    if not _TASK_DEFINITION_ARN.fullmatch(expected_task_definition_arn):
        raise CapacityRecoveryContractError("expected deployed task definition is invalid")
    if not _SHA256.fullmatch(expected_runtime_image_digest):
        raise CapacityRecoveryContractError("expected deployed runtime digest is invalid")
    _require_nonnegative_int(expected_running_count, "expected deployed running count")
    if expected_running_count < 1:
        raise CapacityRecoveryContractError("expected deployed running count must be non-zero")

    services = service_payload.get("services")
    if not isinstance(services, list) or len(services) != 1:
        raise CapacityRecoveryContractError("expected exactly one deployed service")
    service = services[0]
    if not isinstance(service, dict) or service.get("status") != "ACTIVE":
        raise CapacityRecoveryContractError("deployed service is not active")
    _validate_service_safety_shape(service)
    if service.get("taskDefinition") != expected_task_definition_arn:
        raise CapacityRecoveryContractError("deployed service task definition does not match")
    for name in ("desiredCount", "runningCount", "pendingCount"):
        _require_nonnegative_int(service.get(name), f"deployed service {name}")
    if service["desiredCount"] != expected_running_count or service["runningCount"] != expected_running_count:
        raise CapacityRecoveryContractError("deployed service count does not match")
    if service["pendingCount"] != 0:
        raise CapacityRecoveryContractError("deployed service still has pending tasks")

    deployments = service.get("deployments")
    if not isinstance(deployments, list):
        raise CapacityRecoveryContractError("deployed service deployments are malformed")
    primary = [deployment for deployment in deployments if isinstance(deployment, dict) and deployment.get("status") == "PRIMARY"]
    if len(primary) != 1:
        raise CapacityRecoveryContractError("deployed service must have exactly one primary deployment")
    deployment = primary[0]
    if (
        deployment.get("rolloutState") != "COMPLETED"
        or deployment.get("taskDefinition") != expected_task_definition_arn
        or deployment.get("desiredCount") != expected_running_count
        or deployment.get("runningCount") != expected_running_count
        or deployment.get("pendingCount") != 0
    ):
        raise CapacityRecoveryContractError("primary deployment does not prove the expected completed state")

    if running_tasks_payload.get("failures") not in (None, []):
        raise CapacityRecoveryContractError("running task lookup reported failures")
    tasks = running_tasks_payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != expected_running_count:
        raise CapacityRecoveryContractError("running task count does not match deployed service")
    for task in tasks:
        if not isinstance(task, dict) or task.get("lastStatus") != "RUNNING":
            raise CapacityRecoveryContractError("reported task is not running")
        if task.get("taskDefinitionArn") != expected_task_definition_arn:
            raise CapacityRecoveryContractError("running task uses an unexpected task definition")
        containers = task.get("containers")
        matches = [
            container
            for container in containers
            if isinstance(container, dict) and container.get("name") == CONTAINER_NAME
        ] if isinstance(containers, list) else []
        if len(matches) != 1:
            raise CapacityRecoveryContractError("running task has an invalid application container set")
        container = matches[0]
        if container.get("lastStatus") != "RUNNING":
            raise CapacityRecoveryContractError("application container is not running")
        if container.get("imageDigest") != expected_runtime_image_digest:
            raise CapacityRecoveryContractError("running application image digest does not match")

    expected_target_groups = _normalized_load_balancers(service.get("loadBalancers"), "service.loadBalancers")
    if len(target_health_payloads) != len(expected_target_groups):
        raise CapacityRecoveryContractError("target-health evidence does not cover every configured target group")
    for payload in target_health_payloads:
        descriptions = payload.get("TargetHealthDescriptions")
        if not isinstance(descriptions, list) or len(descriptions) < expected_running_count:
            raise CapacityRecoveryContractError("target group does not have enough registered targets")
        for description in descriptions:
            target_health = description.get("TargetHealth") if isinstance(description, dict) else None
            if not isinstance(target_health, dict) or target_health.get("State") != "healthy":
                raise CapacityRecoveryContractError("target group has a non-healthy target")


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
    parser.add_argument("--recovery-safety-manifest-json", type=Path)
    parser.add_argument("--service-json", type=Path)
    parser.add_argument("--task-definition-json", type=Path)
    parser.add_argument("--verify-deployed-runtime", action="store_true")
    parser.add_argument("--deployed-service-json", type=Path)
    parser.add_argument("--running-tasks-json", type=Path)
    parser.add_argument("--target-health-json", type=Path, action="append", default=[])
    parser.add_argument("--expected-deployed-task-definition")
    parser.add_argument("--expected-deployed-runtime-image-digest")
    parser.add_argument("--expected-deployed-running-count")
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
        recovery_safety_manifest = None
        if request.release_mode == "zero_capacity_recovery":
            if args.recovery_safety_manifest_json is None:
                raise CapacityRecoveryContractError("zero-capacity recovery requires recovery-safety-manifest-json")
            recovery_safety_manifest = validate_recovery_safety_manifest(
                _read_json(args.recovery_safety_manifest_json), request.rollback_evidence_sha or ""
            )
        elif args.recovery_safety_manifest_json is not None:
            raise CapacityRecoveryContractError("standard release cannot carry a recovery safety manifest")
        if bool(args.service_json) != bool(args.task_definition_json):
            raise CapacityRecoveryContractError(
                "service-json and task-definition-json must be supplied together"
            )
        if args.service_json and args.task_definition_json:
            validate_live_baseline(
                _read_json(args.service_json),
                _read_json(args.task_definition_json),
                request,
                recovery_safety_manifest,
            )
        deployed_arguments = (
            args.deployed_service_json,
            args.running_tasks_json,
            args.expected_deployed_task_definition,
            args.expected_deployed_runtime_image_digest,
            args.expected_deployed_running_count,
        )
        if args.verify_deployed_runtime:
            if any(argument is None for argument in deployed_arguments) or not args.target_health_json:
                raise CapacityRecoveryContractError("deployed runtime verification evidence is incomplete")
            if not isinstance(args.expected_deployed_running_count, str) or not args.expected_deployed_running_count.isdecimal():
                raise CapacityRecoveryContractError("expected deployed running count must be a non-negative integer")
            validate_deployed_runtime(
                _read_json(args.deployed_service_json),
                _read_json(args.running_tasks_json),
                [_read_json(path) for path in args.target_health_json],
                args.expected_deployed_task_definition,
                args.expected_deployed_runtime_image_digest,
                int(args.expected_deployed_running_count),
            )
        elif any(argument is not None for argument in deployed_arguments) or args.target_health_json:
            raise CapacityRecoveryContractError("deployed runtime evidence requires verify-deployed-runtime")
    except CapacityRecoveryContractError as error:
        parser.error(str(error))

    print(request.release_desired_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
