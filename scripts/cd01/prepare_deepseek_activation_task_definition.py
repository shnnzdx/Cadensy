"""Prepare or validate the controlled CD-02 DeepSeek activation profile.

This helper is deliberately offline.  It does not contact AWS, resolve secret
references, register task definitions, or update an ECS service.  It starts
from the already validated CD-01 Release A runtime-off task definition and
permits exactly one Provider credential reference: ``DEEPSEEK_API_KEY``.
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

ACTIVATION_ENVIRONMENT = {
    "APP_ENV": "production",
    "CHAT_AGENT_RUNTIME": "legacy",
    "MOCK_AI": "0",
    "CADENSY_E2E_FAKE_RUNTIME": "0",
    "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED": "1",
    "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED": "1",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_MODEL": "deepseek-flash",
    "DEEPSEEK_THINKING": "disabled",
}
_ALLOWED_DEEPSEEK_ENVIRONMENT_NAMES = frozenset(
    name for name in ACTIVATION_ENVIRONMENT if name.startswith("DEEPSEEK_")
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


def _validate_secret_reference(secret_reference: str) -> None:
    if not isinstance(secret_reference, str) or not (
        _SSM_PARAMETER_ARN_RE.fullmatch(secret_reference)
        or _SECRETS_MANAGER_ARN_RE.fullmatch(secret_reference)
    ):
        _fail(
            "DEEPSEEK_API_KEY must be an approved-account, approved-region SSM parameter "
            "or Secrets Manager ARN."
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


def validate_release_a_baseline(payload: dict[str, Any]) -> None:
    """Require the exact default-off profile that CD-02 can safely replace."""
    _release_helper.validate_runtime_off_task_definition(payload, profile="release")


def validate_activation_task_definition(payload: dict[str, Any]) -> None:
    """Require the exact, single-provider production activation contract."""
    task_definition = _task_definition(payload)
    container = _application_container(task_definition)
    _release_helper._validate_live_contract(task_definition, container)
    environment, secret_names = _environment_and_secret_names(container)

    for name, expected in ACTIVATION_ENVIRONMENT.items():
        if environment.get(name) != expected:
            _fail(f"DeepSeek activation requires {name}={expected}.")

    for gate in _release_helper.DEFAULT_OFF_GATES:
        expected = "1" if gate in {
            "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED",
            "CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED",
        } else "0"
        if environment.get(gate) != expected:
            _fail(f"DeepSeek activation requires {gate}={expected}.")

    for name in environment:
        upper_name = name.upper()
        if upper_name.startswith(_OTHER_PROVIDER_PREFIXES):
            _fail(f"Non-DeepSeek provider configuration {name!r} is forbidden.")
        if upper_name.startswith("DEEPSEEK_") and name not in _ALLOWED_DEEPSEEK_ENVIRONMENT_NAMES:
            _fail(f"Unexpected DeepSeek environment setting {name!r} is forbidden.")
        if _release_helper._is_provider_credential(name):
            _fail("Provider credentials must be ECS secret references, never plaintext environment data.")

    if secret_names.count("DEEPSEEK_API_KEY") != 1:
        _fail("DeepSeek activation requires exactly one DEEPSEEK_API_KEY secret reference.")
    for name in secret_names:
        if _release_helper._is_provider_credential(name) and name != "DEEPSEEK_API_KEY":
            _fail(f"Non-DeepSeek provider credential {name!r} is forbidden.")
        if _release_helper._is_unclassified_api_key(name):
            _fail(f"Unclassified API-key secret {name!r} is forbidden.")

    deepseek_secret = next(
        entry
        for entry in container["secrets"]
        if entry.get("name") == "DEEPSEEK_API_KEY"
    )
    _validate_secret_reference(deepseek_secret.get("valueFrom"))


def prepare_activation_task_definition(
    payload: dict[str, Any], image: str, deepseek_api_key_secret_arn: str
) -> dict[str, Any]:
    """Clone one Release A task definition into the narrowly approved CD-02 profile."""
    validate_release_a_baseline(payload)
    _validate_secret_reference(deepseek_api_key_secret_arn)

    # Reuse the reviewed registrable-field and digest-pinning conversion.  Its
    # output has already removed any Provider credentials and pinned every
    # default-off gate before the two approved gates are selectively enabled.
    prepared = _release_helper.prepare_task_definition(copy.deepcopy(payload), image)
    container = _application_container(prepared)
    order, environment = _release_helper._environment_map(container)
    secrets = container.get("secrets", [])
    if not isinstance(secrets, list):
        _fail("Container secrets must be a list.")
    if any(entry.get("name") == "DEEPSEEK_API_KEY" for entry in secrets):
        _fail("Release A baseline must not already contain DEEPSEEK_API_KEY.")

    environment.update(ACTIVATION_ENVIRONMENT)
    container["environment"] = [
        {"name": name, "value": environment[name]}
        for name in [*order, *(name for name in environment if name not in order)]
    ]
    secrets.append({"name": "DEEPSEEK_API_KEY", "valueFrom": deepseek_api_key_secret_arn})
    container["secrets"] = secrets
    validate_activation_task_definition(prepared)
    return prepared


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--deepseek-api-key-secret-arn")
    parser.add_argument("--validate-release-a-baseline", action="store_true")
    parser.add_argument("--validate-activation-profile", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        modes = int(args.validate_release_a_baseline) + int(args.validate_activation_profile)
        if modes:
            if modes != 1 or args.output or args.image or args.deepseek_api_key_secret_arn:
                _fail("Validation modes do not accept output, image, or secret-reference arguments.")
            if args.validate_release_a_baseline:
                validate_release_a_baseline(payload)
            else:
                validate_activation_task_definition(payload)
            return
        if not args.output or not args.image or not args.deepseek_api_key_secret_arn:
            _fail("Activation generation requires --output, --image, and --deepseek-api-key-secret-arn.")
        prepared = prepare_activation_task_definition(
            payload, args.image, args.deepseek_api_key_secret_arn
        )
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"CD-02 DeepSeek activation contract failed: {exc}") from exc
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
