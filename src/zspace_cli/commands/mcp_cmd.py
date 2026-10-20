"""`zs mcp`：启动 MCP Server、查看工具列表、生成客户端配置."""

from __future__ import annotations

import json
import shutil
import sys

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import console

mcp_app = typer.Typer(
    name="mcp",
    help="MCP Server：让 Claude / Cursor / Antigravity 等 AI 助手操作极空间",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = mcp_app


@mcp_app.command("serve")
def serve() -> None:
    """启动 MCP stdio 服务（由 AI 客户端拉起，一般不需要手动运行）."""
    from zspace_cli.mcp.server import serve as _serve

    _serve()


@mcp_app.command("tools")
def tools() -> None:
    """列出对外暴露的 MCP 工具."""
    from zspace_cli.mcp.server import TOOLS

    t = Table(title="MCP 工具", box=box.SIMPLE_HEAVY)
    t.add_column("名称", no_wrap=True)
    t.add_column("说明", overflow="fold")
    for tool in TOOLS:
        t.add_row(tool["name"], tool["description"])
    console.print(t)


@mcp_app.command("config")
def config() -> None:
    """输出可粘贴到 AI 客户端的 mcpServers 配置片段."""
    zs = shutil.which("zs") or f"{sys.prefix}/bin/zs"
    snippet = {"mcpServers": {"zspace": {"command": zs, "args": ["mcp", "serve"]}}}
    console.print_json(json.dumps(snippet, ensure_ascii=False))
