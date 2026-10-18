"""ORM contract for the PR-06B.1B private-conversation persistence boundary."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.db.models import (
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    ConversationSubmissionRecovery,
)


def _named_checks(model: type) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }


def test_conversation_root_preserves_membership_incarnation_ownership() -> None:
    columns = Conversation.__table__.c
    assert {column.name for column in Conversation.__table__.columns} >= {
        "id",
        "trip_id",
        "owner_membership_id",
        "scope",
        "context_key",
        "status",
        "last_qualifying_activity_at",
        "retention_expires_at",
        "deleted_at",
        "purge_due_at",
        "created_at",
        "updated_at",
    }
    composite_owner_fk = next(
        constraint
        for constraint in Conversation.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_conversation_owner_membership_same_trip"
    )
    assert tuple(composite_owner_fk.column_keys) == ("owner_membership_id", "trip_id")
    assert tuple(element.column.name for element in composite_owner_fk.elements) == ("id", "trip_id")
    assert {
        constraint.name
        for constraint in Conversation.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    } >= {"uq_conversation_id_trip_owner"}
    indexes = {index.name: index for index in Conversation.__table__.indexes}
    assert indexes["ux_conversation_active_natural_identity"].unique is True
    assert "status = 'active'" in str(
        indexes["ux_conversation_active_natural_identity"].dialect_options["postgresql"]["where"]
    )
    checks = _named_checks(Conversation)
    assert {"ck_conversation_scope", "ck_conversation_status", "ck_conversation_context_key", "ck_conversation_retention_window", "ck_conversation_deletion_lifecycle"} <= set(checks)
    assert columns.owner_membership_id.nullable is False


def test_message_and_option_schema_preserve_order_and_safe_parentage() -> None:
    message_constraints = {
        constraint.name
        for constraint in ConversationMessage.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    assert {"uq_conversation_message_sequence", "uq_conversation_message_id_conversation"} <= message_constraints
    message_checks = _named_checks(ConversationMessage)
    assert {"ck_conversation_message_sequence_nonnegative", "ck_conversation_message_role", "ck_conversation_message_state", "ck_conversation_message_plan_revision_nonnegative", "ck_conversation_message_facts_revision_nonnegative"} <= set(message_checks)
    option_constraints = {
        constraint.name
        for constraint in ConversationMessageOption.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    assert "uq_conversation_message_option_ordinal" in option_constraints
    option_checks = _named_checks(ConversationMessageOption)
    assert {"ck_conversation_message_option_ordinal_nonnegative", "ck_conversation_message_option_patch_object"} <= set(option_checks)


def test_submission_and_recovery_are_bound_to_one_conversation_lifecycle() -> None:
    submission_constraints = {
        constraint.name
        for constraint in ConversationSubmission.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    assert {
        "uq_conversation_submission_idempotency",
        "uq_conversation_submission_user_message",
        "uq_conversation_submission_assistant_message",
    } <= submission_constraints
    submission_fks = {
        constraint.name
        for constraint in ConversationSubmission.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name
    }
    assert {
        "fk_conversation_submission_conversation_owner",
        "fk_conversation_submission_user_message_same_conversation",
        "fk_conversation_submission_assistant_message_same_conversation",
    } <= submission_fks
    submission_indexes = {index.name: index for index in ConversationSubmission.__table__.indexes}
    assert submission_indexes["ux_conversation_submission_one_running"].unique is True
    assert "state = 'running'" in str(
        submission_indexes["ux_conversation_submission_one_running"].dialect_options["postgresql"]["where"]
    )
    submission_checks = _named_checks(ConversationSubmission)
    assert {"ck_conversation_submission_state", "ck_conversation_submission_attempt_positive", "ck_conversation_submission_fence_positive", "ck_conversation_submission_lease_lifecycle"} <= set(submission_checks)

    recovery_constraints = {
        constraint.name
        for constraint in ConversationSubmissionRecovery.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    assert "uq_conversation_submission_recovery_key" in recovery_constraints
    recovery_checks = _named_checks(ConversationSubmissionRecovery)
    assert {"ck_conversation_submission_recovery_state", "ck_conversation_submission_recovery_operation_version_positive", "ck_conversation_submission_recovery_attempt_positive", "ck_conversation_submission_recovery_fence_positive"} <= set(recovery_checks)
