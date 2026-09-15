"""
OmniCache Model Context Protocol (MCP) Server Compatibility Shim.
Re-exports process_mcp_jsonrpc, TOOLS_METADATA, and run_stdio_server from mcp.server.
"""

from mcp.server import (  # noqa: F401
    process_mcp_jsonrpc,
    TOOLS_METADATA,
    run_stdio_server,
    handle_tool_call,
)

__all__ = [
    "process_mcp_jsonrpc",
    "TOOLS_METADATA",
    "run_stdio_server",
    "handle_tool_call",
]

if __name__ == "__main__":
    run_stdio_server()
