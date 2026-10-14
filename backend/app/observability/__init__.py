"""Small, dependency-free observability contracts for Cadensy."""

from .telemetry import SafeTelemetryEmitter, TelemetryContext, emit_event_safely, emit_safely

__all__ = ["SafeTelemetryEmitter", "TelemetryContext", "emit_event_safely", "emit_safely"]
