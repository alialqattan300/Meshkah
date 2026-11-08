"""MCP server: Claude <-> MetaTrader 5 (Exness or any MT5 broker), DEMO only.

Windows, with MT5 open and logged in to a demo account:
    pip install MetaTrader5 "mcp>=2.3,<3"
    claude mcp add exness-mt5 -- python .claude/skills/forex-trader/mt5/server.py

Tools: mt5_status, mt5_quote, mt5_positions (read only),
mt5_preview_order (read only), mt5_place_order and mt5_close_position
(both need your approval every time).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge import Bridge, BridgeError, to_text  # noqa: E402

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit("MetaTrader5 is not installed: pip install MetaTrader5 (Windows only).")

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server
from mcp.types import ToolAnnotations

bridge = Bridge(mt5)
app = Server("exness-mt5", instructions=(
    "MetaTrader 5 bridge for the forex-trader skill. DEMO accounts only. "
    "Before mt5_place_order, show the user mt5_preview_order in plain words and get an explicit yes for that exact order."))

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def run(fn, *a):
    try:
        return to_text(fn(*a))
    except BridgeError as e:
        return "NOT SENT: %s" % e


@app.tool(annotations=READ)
def mt5_status() -> str:
    """The MT5 account this bridge is attached to: demo check, balance, equity, leverage, algo trading on or off."""
    return run(bridge.status)


@app.tool(annotations=READ)
def mt5_quote(symbol: str) -> str:
    """Live bid, ask and spread in pips for a pair, gold or oil on this MT5 account (finds Exness suffixes like EURUSDm)."""
    return run(bridge.quote, symbol)


@app.tool(annotations=READ)
def mt5_positions() -> str:
    """Open positions on the demo account."""
    return run(bridge.positions)


@app.tool(annotations=READ)
def mt5_preview_order(fingerprint: str) -> str:
    """What mt5_place_order would send for the order in forex/order.json (needs a fresh risk PASS). Sends nothing."""
    return run(bridge.preview, fingerprint)


@app.tool(annotations=WRITE)
def mt5_place_order(fingerprint: str) -> str:
    """Send the risk-passed order with this fingerprint to the MT5 DEMO account, once, with its stop-loss and take-profit.
    Only after the user typed yes for this exact order in the chat."""
    return run(bridge.place, fingerprint)


@app.tool(annotations=WRITE)
def mt5_close_position(ticket: int) -> str:
    """Close one open position on the demo account at market. Only after the user said yes to closing it."""
    return run(bridge.close, ticket)


if __name__ == "__main__":
    app.run()
