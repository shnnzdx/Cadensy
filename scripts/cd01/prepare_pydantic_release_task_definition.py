"""Prepare or validate the offline contract for a Pydantic production revision.

The helper deliberately has no AWS client.  It clones a reviewed live Legacy
profile, retaining all registrable fields, and permits only the immutable image
and ``CHAT_AGENT_RUNTIME`` selector to change.  Secret values are never read.
"""

from __future__ import annotations

import argparse
import copy
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

_SSM_PARAMETER_ARN_RE = re.compile(
    r"^arn:aws:ssm:us-east-1:448678332746:parameter/[A-Za-z0-9_./+=,@-]+$"
)
_SECRETS_MANAGER_ARN_RE = re.compile(
    r"^arn:aws:secretsmanager:us-east-1:448678332746:secret:[A-Za-z0-9/_+=.@-]+$"
)
_OLLAMA_NAMESPACE_RE = re.compile(r"^OLLAMA(?:_|$)", re.IGNORECASE)
_AI_PROVIDER_SELECTOR_RE = re.compile(r"(?:^|_)AI_PROVIDER$", re.IGNORECASE)

_REQUIRED_RUNTIME = {
    "APP_ENV": "production",
    "DISABLE_SCHEDULER": "1",
    "DEV_ALLOW_MEMBERSHIP_HEADER": "0",
    "MOCK_AI": "0",
    "CADENSY_E2E_FAKE_RUNTIME": "0",
    "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED": "1",
    "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED": "1",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_MODEL": "deepseek-flash",
    "DEEPSEEK_THINKING": "disabled",
}
_ALLOWED_DEEPSEEK_ENVIRONMENT_NAMES = frozenset(
    {"DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_THINKING"}
)
_OTHER_PROVIDER_PREFIXES = tuple(
    f"{prefix}_"
    for prefix in _release_helper.AI_PROVIDER_CREDENTIAL_PREFIXES
    if prefix != "DEEPSEEK"
)


def _fail(message: str) -> None:
    raise ContractError(message)


def _task_definition(payload: dict[str, Any]) -> dict[str, Any]:
    task_definition = payload.get("taskDefinition", payload)
    if not isinstance(task_definition, dict):
        _fail("Task-definition input must be a JSON object.")
    return task_definition


def _application_container(payload: dict[str, Any]) -> dict[str, Any]:
    return _release_helper._application_container(_task_definition(payload))


def _validate_expected_baseline(
    payload: dict[str, Any], expected_baseline_arn: str | None
) -> None:
    if not expected_baseline_arn:
        return
    actual = _task_definition(payload).get("taskDefinitionArn")
    if actual != expected_baseline_arn:
        _fail("Task definition does not match the expected current baseline ARN.")


def _validate_secret_reference(secret_reference: object) -> None:
    if not isinstance(secret_reference, str) or not (
        _SSM_PARAMETER_ARN_RE.fullmatch(secret_reference)
        or _SECRETS_MANAGER_ARN_RE.fullmatch(secret_reference)
    ):
        _fail(
            "DEEPSEEK_API_KEY must be an approved-account, approved-region SSM parameter "
            "or Secrets Manager ARN."
        )


def _is_retired_provider_configuration(name: object) -> bool:
    return isinstance(name, str) and (
        bool(_OLLAMA_NAMESPACE_RE.match(name))
        or bool(_AI_PROVIDER_SELECTOR_RE.search(name))
    )


def _environment_and_secret_names(container: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    _, environment = _release_helper._environment_map(container)
    secret_names = _release_helper._secret_names(container)
    collisions = sorted(set(environment).intersection(secret_names))
    if collisions:
        _fail(
            "Variables configured as both environment and secrets are forbidden: "
            + ", ".join(repr(name) for name in collisions)
            + "."
        )
    return environment, secret_names


def _validate_common_provider_contract(container: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    environment, secret_names = _environment_and_secret_names(container)
    for name in environment:
        upper_name = name.upper()
        if _is_retired_provider_configuration(name):
            _fail(f"Retired provider configuration {name!r} is forbidden.")
        if upper_name.startswith(_OTHER_PROVIDER_PREFIXES):
            _fail(f"Non-DeepSeek provider configuration {name!r} is forbidden.")
        if upper_name.startswith("DEEPSEEK_") and name not in _ALLOWED_DEEPSEEK_ENVIRONMENT_NAMES:
            _fail(f"Unexpected DeepSeek environment setting {name!r} is forbidden.")
        if _release_helper._is_provider_credential(name):
            _fail("Provider credentials must be ECS secret references, never plaintext environment data.")
        if _release_helper._is_unclassified_api_key(name):
            _fail(f"Unclassified API-key environment variable {name!r} is forbidden.")

    if secret_names.count("DEEPSEEK_API_KEY") != 1:
        _fail("Pydantic production profile requires exactly one DEEPSEEK_API_KEY secret reference.")
    for name in secret_names:
        if _is_retired_provider_configuration(name):
            _fail(f"Retired provider secret reference {name!r} is forbidden.")
        if _release_helper._is_provider_credential(name) and name != "DEEPSEEK_API_KEY":
            _fail(f"Non-DeepSeek provider credential {name!r} is forbidden.")
        if _release_helper._is_unclassified_api_key(name):
            _fail(f"Unclassified API-key secret {name!r} is forbidden.")

    deepseek_secret = next(
        entry for entry in container["secrets"] if entry.get("name") == "DEEPSEEK_API_KEY"
    )
    _validate_secret_reference(deepseek_secret.get("valueFrom"))
    return environment, secret_names


def _validate_runtime_profile(
    payload: dict[str, Any], *, selector: str, expected_baseline_arn: str | None = None
) -> None:
    _validate_expected_baseline(payload, expected_baseline_arn)
    task_definition = _task_definition(payload)
    container = _application_container(task_definition)
    _release_helper._validate_live_contract(task_definition, container)
    environment, _ = _validate_common_provider_contract(container)
    for name, expected in {**_REQUIRED_RUNTIME, "CHAT_AGENT_RUNTIME": selector}.items():
        if environment.get(name) != expected:
            _fail(f"Pydantic production profile requires {name}={expected}.")
    image = container.get("image")
    if not isinstance(image, str) or not _release_helper.APPROVED_IMAGE_RE.fullmatch(image):
        _fail("Task definition must use the approved backend ECR repository pinned by sha256 digest.")


def validate_legacy_rollback_task_definition(
    payload: dict[str, Any], *, expected_baseline_arn: str | None = None
) -> None:
    """Validate the exact healthy Legacy profile used for application rollback."""
    _validate_runtime_profile(payload, selector="legacy", expected_baseline_arn=expected_baseline_arn)


def validate_pydantic_task_definition(payload: dict[str, Any]) -> None:
    """Validate the production Pydantic selector profile without contacting AWS."""
    _validate_runtime_profile(payload, selector="pydantic")


def prepare_pydantic_task_definition(
    payload: dict[str, Any], image: str, *, expected_baseline_arn: str | None = None
) -> dict[str, Any]:
    """Clone a verified Legacy baseline with only the digest and selector changed."""
    if not _release_helper.APPROVED_IMAGE_RE.fullmatch(image):
        _fail("Image must be the approved backend ECR repository pinned by sha256 digest.")
    validate_legacy_rollback_task_definition(
        payload, expected_baseline_arn=expected_baseline_arn
    )
    task_definition = _task_definition(payload)
    prepared = {
        field: copy.deepcopy(task_definition[field])
        for field in _release_helper.REGISTRABLE_FIELDS
        if field in task_definition
    }
    target = _application_container(prepared)
    order, environment = _release_helper._environment_map(target)
    target["image"] = image
    environment["CHAT_AGENT_RUNTIME"] = "pydantic"
    target["environment"] = [
        {"name": name, "value": environment[name]}
        for name in [*order, *(name for name in environment if name not in order)]
    ]
    validate_pydantic_task_definition(prepared)
    return prepared


def validate_pydantic_transition(
    baseline: dict[str, Any], candidate: dict[str, Any], image: str
) -> None:
    """Reject every transition difference other than image and runtime selector."""
    validate_legacy_rollback_task_definition(baseline)
    validate_pydantic_task_definition(candidate)
    expected = prepare_pydantic_task_definition(baseline, image)
    baseline_environment = _release_helper._environment_map(_application_container(baseline))[1]
    candidate_environment = _release_helper._environment_map(_application_container(candidate))[1]
    for name in sorted(set(baseline_environment).union(candidate_environment)):
        expected_value = "pydantic" if name == "CHAT_AGENT_RUNTIME" else baseline_environment.get(name)
        if candidate_environment.get(name) != expected_value:
            _fail(f"Pydantic transition contains an unapproved environment change for {name!r}.")
    if candidate != expected:
        _fail("Pydantic transition changes task-definition configuration outside the approved image/runtime changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--expected-baseline-arn")
    parser.add_argument("--validate-legacy-profile", action="store_true")
    parser.add_argument("--validate-pydantic-profile", action="store_true")
    parser.add_argument("--validate-transition-from", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        modes = sum(
            bool(value)
            for value in (
                args.validate_legacy_profile,
                args.validate_pydantic_profile,
                args.validate_transition_from,
            )
        )
        if modes:
            if modes != 1 or args.output:
                _fail("Validation modes are mutually exclusive and do not accept --output.")
            if args.validate_legacy_profile:
                if args.image:
                    _fail("Legacy-profile validation does not accept --image.")
                validate_legacy_rollback_task_definition(
                    payload, expected_baseline_arn=args.expected_baseline_arn
                )
            elif args.validate_pydantic_profile:
                if args.image or args.expected_baseline_arn:
                    _fail("Pydantic-profile validation does not accept image or baseline arguments.")
                validate_pydantic_task_definition(payload)
            else:
                if not args.image or args.expected_baseline_arn:
                    _fail("Transition validation requires --image only.")
                baseline = json.loads(args.validate_transition_from.read_text(encoding="utf-8"))
                validate_pydantic_transition(baseline, payload, args.image)
            return
        if not args.output or not args.image:
            _fail("Pydantic task-definition generation requires --output and --image.")
        prepared = prepare_pydantic_task_definition(
            payload, args.image, expected_baseline_arn=args.expected_baseline_arn
        )
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"CD-03 Pydantic task-definition contract failed: {exc}") from exc
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
