#!/usr/bin/env python3
"""Every tool of the MCP server (tools/mcp/morlet_mcp.py) is documented in docs/mcp.md and on the website
(docs/docs.html, "Connect an AI agent (MCP)"), and neither documents a tool the server does not have.
Standard library only; run from the repository root."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "tools/mcp/morlet_mcp.py").read_text()
tools = re.findall(r"@mcp\.tool\([^)]*\)\s*\n(?:@[^\n]*\n)*def (\w+)\(", src)
md = set(re.findall(r"^\| `(\w+)` \|", (ROOT / "docs/mcp.md").read_text(), re.M))
html = (ROOT / "docs/docs.html").read_text()
i = html.index('id="connect-an-ai-agent-mcp"')
site = set(re.findall(r"<td><code>(\w+)</code></td>", html[i:html.find("</table>", i)]))
problems = [f"{where}: {t} {what}" for where, documented in (("docs/mcp.md", md), ("docs/docs.html", site))
            for t, what in [(t, "is a server tool but not documented") for t in tools if t not in documented]
            + [(t, "is documented but not a server tool") for t in sorted(documented - set(tools))]]
if not tools:
    problems.append("no @mcp.tool functions found in tools/mcp/morlet_mcp.py")
print("\n".join(problems) or f"MCP docs: all {len(tools)} tools documented in docs/mcp.md and docs/docs.html")
sys.exit(1 if problems else 0)
