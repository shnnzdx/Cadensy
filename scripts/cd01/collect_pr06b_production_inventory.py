"""Collect a redacted, read-only PR-06B production infrastructure inventory.

The collector permits a deliberately small AWS CLI allow-list.  It never calls
an operation that creates, changes, starts, restores, scales, registers, runs,
or retrieves a secret/parameter value.  Its JSON output contains resource
metadata and secret *names* only, suitable for the offline recovery validator.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable


READ_ONLY_OPERATIONS = frozenset(
    {
        ("sts", "get-caller-identity"),
        ("ecs", "describe-clusters"),
        ("ecs", "describe-services"),
        ("ecs", "describe-task-definition"),
        ("elbv2", "describe-target-groups"),
        ("elbv2", "describe-target-health"),
        ("rds", "describe-db-instances"),
        ("rds", "describe-db-snapshots"),
        ("rds", "describe-db-subnet-groups"),
        ("ec2", "describe-security-groups"),
        ("ecr", "describe-images"),
        ("kms", "list-aliases"),
        ("iam", "get-role"),
        ("iam", "list-attached-role-policies"),
        ("iam", "list-role-policies"),
        ("ssm", "describe-parameters"),
        ("secretsmanager", "list-secrets"),
        ("logs", "describe-log-groups"),
        ("cloudwatch", "describe-alarms"),
        ("events", "list-rules"),
    }
)


class InventoryError(RuntimeError):
    """A required read-only inventory operation could not be completed."""


def _sanitize_task_definition(payload: dict[str, Any]) -> dict[str, Any]:
    definition = payload.get("taskDefinition", {})
    containers = []
    for container in definition.get("containerDefinitions", []):
        containers.append(
            {
                "name": container.get("name"),
                "image": container.get("image"),
                "environment_names": sorted(
                    entry.get("name") for entry in container.get("environment", []) if entry.get("name")
                ),
                "secret_names": sorted(
                    entry.get("name") for entry in container.get("secrets", []) if entry.get("name")
                ),
                "log_group": container.get("logConfiguration", {}).get("options", {}).get("awslogs-group"),
            }
        )
    return {
        "family": definition.get("family"),
        "revision": definition.get("revision"),
        "task_role_arn": definition.get("taskRoleArn"),
        "execution_role_arn": definition.get("executionRoleArn"),
        "network_mode": definition.get("networkMode"),
        "requires_compatibilities": definition.get("requiresCompatibilities", []),
        "containers": containers,
    }


def _sanitize_service(service: dict[str, Any]) -> dict[str, Any]:
    network = service.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
    return {
        "name": service.get("serviceName"),
        "status": service.get("status"),
        "desired_count": service.get("desiredCount"),
        "running_count": service.get("runningCount"),
        "pending_count": service.get("pendingCount"),
        "task_definition": service.get("taskDefinition"),
        "subnet_ids": network.get("subnets", []),
        "security_group_ids": network.get("securityGroups", []),
        "assign_public_ip": network.get("assignPublicIp"),
        "target_group_arns": [item.get("targetGroupArn") for item in service.get("loadBalancers", [])],
        "circuit_breaker": service.get("deploymentConfiguration", {}).get("deploymentCircuitBreaker", {}),
        "minimum_healthy_percent": service.get("deploymentConfiguration", {}).get("minimumHealthyPercent"),
        "maximum_percent": service.get("deploymentConfiguration", {}).get("maximumPercent"),
        "health_check_grace_period_seconds": service.get("healthCheckGracePeriodSeconds"),
    }


def _sanitize_rds_instance(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": instance.get("DBInstanceIdentifier"),
        "status": instance.get("DBInstanceStatus"),
        "engine": instance.get("Engine"),
        "engine_version": instance.get("EngineVersion"),
        "storage_encrypted": instance.get("StorageEncrypted"),
        "kms_key_id": instance.get("KmsKeyId"),
        "backup_retention_days": instance.get("BackupRetentionPeriod"),
        "multi_az": instance.get("MultiAZ"),
        "availability_zone": instance.get("AvailabilityZone"),
        "db_subnet_group": instance.get("DBSubnetGroup", {}).get("DBSubnetGroupName"),
        "security_group_ids": [item.get("VpcSecurityGroupId") for item in instance.get("VpcSecurityGroups", [])],
        "parameter_groups": [item.get("DBParameterGroupName") for item in instance.get("DBParameterGroups", [])],
        "publicly_accessible": instance.get("PubliclyAccessible"),
        "ca_certificate_identifier": instance.get("CACertificateIdentifier"),
        "latest_restorable_time": instance.get("LatestRestorableTime"),
    }


def _sanitize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot.get("DBSnapshotIdentifier"),
        "status": snapshot.get("Status"),
        "created_at": snapshot.get("SnapshotCreateTime"),
        "encrypted": snapshot.get("Encrypted"),
        "kms_key_id": snapshot.get("KmsKeyId"),
        "engine": snapshot.get("Engine"),
        "engine_version": snapshot.get("EngineVersion"),
        "allocated_storage": snapshot.get("AllocatedStorage"),
        "snapshot_type": snapshot.get("SnapshotType"),
    }


def _short_error(error: subprocess.CalledProcessError) -> dict[str, str]:
    return {"type": "aws_cli_error", "detail": (error.stderr or error.stdout or "AWS CLI failed").strip()}


class AwsCli:
    def __init__(self, *, region: str, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.region = region
        self._runner = runner

    def call(self, service: str, operation: str, *args: str) -> dict[str, Any]:
        if (service, operation) not in READ_ONLY_OPERATIONS:
            raise InventoryError(f"operation is not approved for read-only inventory: {service} {operation}")
        command = ["aws", service, operation, "--region", self.region, "--output", "json", *args]
        try:
            result = self._runner(command, check=True, text=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            return {"error": _short_error(exc)}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise InventoryError(f"AWS CLI returned non-JSON for {service} {operation}") from exc


def _resource_or_error(payload: dict[str, Any], key: str) -> Any:
    if "error" in payload:
        return payload
    return payload.get(key, payload)


def collect_inventory(
    client: AwsCli,
    *,
    cluster: str,
    service: str,
    db_instance: str,
    ecr_repository: str,
    ssm_path: str,
) -> dict[str, Any]:
    """Gather only metadata needed to plan, not execute, PR-06B recovery."""

    identity = client.call("sts", "get-caller-identity")
    clusters = client.call("ecs", "describe-clusters", "--clusters", cluster)
    services = client.call("ecs", "describe-services", "--cluster", cluster, "--services", service)
    rds = client.call("rds", "describe-db-instances", "--db-instance-identifier", db_instance)
    snapshots = client.call("rds", "describe-db-snapshots", "--db-instance-identifier", db_instance)
    images = client.call("ecr", "describe-images", "--repository-name", ecr_repository)
    aliases = client.call("kms", "list-aliases")
    parameters = client.call("ssm", "describe-parameters", "--parameter-filters", f"Key=Name,Option=BeginsWith,Values={ssm_path}")
    secrets = client.call("secretsmanager", "list-secrets")
    alarms = client.call("cloudwatch", "describe-alarms")
    rules = client.call("events", "list-rules")

    raw_services = _resource_or_error(services, "services")
    raw_instances = _resource_or_error(rds, "DBInstances")
    raw_snapshots = _resource_or_error(snapshots, "DBSnapshots")
    result: dict[str, Any] = {
        "classification": "CURRENT_AWS_VERIFIED_METADATA_ONLY",
        "identity": _resource_or_error(identity, ""),
        "ecs": {
            "cluster": _resource_or_error(clusters, "clusters"),
            "service": raw_services if isinstance(raw_services, dict) else [_sanitize_service(item) for item in raw_services],
        },
        "rds": {
            "instance": raw_instances if isinstance(raw_instances, dict) else [_sanitize_rds_instance(item) for item in raw_instances],
            "snapshots": raw_snapshots if isinstance(raw_snapshots, dict) else [_sanitize_snapshot(item) for item in raw_snapshots],
        },
        "ecr": _resource_or_error(images, "imageDetails"),
        "kms_aliases": _resource_or_error(aliases, "Aliases"),
        "ssm_parameters": parameters if "error" in parameters else [
            {"name": item.get("Name"), "type": item.get("Type"), "key_id": item.get("KeyId")}
            for item in parameters.get("Parameters", [])
            if isinstance(item, dict)
        ],
        "secrets": secrets if "error" in secrets else [
            {"name": item.get("Name"), "arn": item.get("ARN"), "kms_key_id": item.get("KmsKeyId")}
            for item in secrets.get("SecretList", [])
            if isinstance(item, dict)
        ],
        "cloudwatch": {
            "alarms": _resource_or_error(alarms, "MetricAlarms"),
            "eventbridge_rules": _resource_or_error(rules, "Rules"),
        },
    }

    if isinstance(raw_services, list) and len(raw_services) == 1:
        service_record = raw_services[0]
        task_definition = service_record.get("taskDefinition")
        if task_definition:
            task = client.call("ecs", "describe-task-definition", "--task-definition", task_definition)
            result["ecs"]["task_definition"] = _sanitize_task_definition(task) if "error" not in task else task
            if "error" not in task:
                log_groups = {
                    container.get("logConfiguration", {}).get("options", {}).get("awslogs-group")
                    for container in task.get("taskDefinition", {}).get("containerDefinitions", [])
                }
                result["cloudwatch"]["log_groups"] = {
                    log_group: client.call("logs", "describe-log-groups", "--log-group-name-prefix", log_group)
                    for log_group in sorted(value for value in log_groups if value)
                }
        target_groups = service_record.get("loadBalancers", [])
        if target_groups:
            target_group_arn = target_groups[0].get("targetGroupArn")
            if target_group_arn:
                result["ecs"]["target_group"] = client.call(
                    "elbv2", "describe-target-groups", "--target-group-arns", target_group_arn
                )
                result["ecs"]["target_health"] = client.call(
                    "elbv2", "describe-target-health", "--target-group-arn", target_group_arn
                )
    if isinstance(raw_instances, list) and len(raw_instances) == 1:
        instance = raw_instances[0]
        subnet_group = instance.get("DBSubnetGroup", {}).get("DBSubnetGroupName")
        if subnet_group:
            result["rds"]["subnet_group"] = client.call(
                "rds", "describe-db-subnet-groups", "--db-subnet-group-name", subnet_group
            )
        group_ids = [item.get("VpcSecurityGroupId") for item in instance.get("VpcSecurityGroups", [])]
        if group_ids:
            result["rds"]["security_groups"] = client.call("ec2", "describe-security-groups", "--group-ids", *group_ids)
    task_definition = result["ecs"].get("task_definition", {})
    if isinstance(task_definition, dict):
        role_names = []
        for role_arn in (task_definition.get("task_role_arn"), task_definition.get("execution_role_arn")):
            if isinstance(role_arn, str) and "/" in role_arn:
                role_names.append(role_arn.rsplit("/", 1)[1])
        result["ecs"]["iam_roles"] = {
            name: {
                "role": client.call("iam", "get-role", "--role-name", name),
                "attached": client.call("iam", "list-attached-role-policies", "--role-name", name),
                "inline": client.call("iam", "list-role-policies", "--role-name", name),
            }
            for name in role_names
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="tripsync-cluster")
    parser.add_argument("--service", default="tripsync-backend-service")
    parser.add_argument("--db-instance", default="tripsync-postgres")
    parser.add_argument("--ecr-repository", default="tripsync-backend")
    parser.add_argument("--ssm-path", default="/tripsync/backend/prod/")
    args = parser.parse_args()
    try:
        inventory = collect_inventory(
            AwsCli(region=args.region),
            cluster=args.cluster,
            service=args.service,
            db_instance=args.db_instance,
            ecr_repository=args.ecr_repository,
            ssm_path=args.ssm_path,
        )
    except InventoryError as exc:
        raise SystemExit(f"PR-06B read-only inventory failed: {exc}") from exc
    args.output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
