"""Test-only alternative Runtime that knows only the neutral capability API."""

from __future__ import annotations

from app.agents.runtime_contract import ReadTripCapability, RuntimeReadTool


class FakeAlternativeRuntime:
    """Consumes scoped tools without importing Legacy AgentTool internals."""

    def read_first_tool(self, capability: ReadTripCapability) -> object:
        def invoke_first(tools: tuple[RuntimeReadTool, ...]) -> object:
            assert tools
            return tools[0].invoke()

        return capability.run_with_read_only_tools(invoke_first)
