"""极空间 MCP Server（零额外依赖，stdio 上的 JSON-RPC 2.0，按行分隔）.

暴露的工具：
- zspace_status：存储池与磁盘空间概览
- zspace_api_search：在内置 2,004 个接口目录中检索
- zspace_api_call：按目录元数据调用任意接口

安全：名称带有写操作特征（delete/remove/stop/reboot/shutdown 等）的接口，
必须显式传入 `confirm_write=true` 才会真正发出请求，否则只返回提示。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from zspace_cli import __version__
from zspace_cli.core.catalog import get_catalog
from zspace_cli.core.client import ZSpaceClient

PROTOCOL_VERSION = "2024-11-05"

# 命中即视为可能修改数据/状态的接口
_WRITE_PATTERN = re.compile(
    r"(delete|del/|del$|remove|rm$|stop|start|reboot|shutdown|set|add|create|update|modify|edit|"
    r"clean|clear|save|install|upload|move|copy|kill|unlock|reset|login|logout|run|enable|disable|"
    r"close|bind|unbind|apply|submit|sync|scan|rebuild|format|restore|cancel|pause|resume|switch|"
    r"toggle|send|wake|rename|mkdir|newdir|open|(?:^|[/_])op(?:$|[/_])|action|operate|control)",
    re.IGNORECASE,
)

# 只有名字明确属于只读语义的接口才允许免确认调用（白名单优先，黑名单兜底）
_READ_PATTERN = re.compile(
    r"(list|info|status|get|stat|query|polling|summary|detail|config|setting|show|check|"
    r"version|overview|home|count|names|search|latest|statics|usage)",
    re.IGNORECASE,
)


def _is_read_only(path: str) -> bool:
    """接口既命中只读白名单、又不命中写操作黑名单，才视为只读."""
    return bool(_READ_PATTERN.search(path)) and not _WRITE_PATTERN.search(path)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "zspace_status",
        "description": "获取极空间 NAS 的存储池与磁盘空间概览（只读）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zspace_api_search",
        "description": "在极空间内置的 2,004 个接口目录中按关键词检索（URL、函数名、参数名、模块名），返回方法、路径与参数。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "检索关键词，如 docker、迅雷、album"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "zspace_api_call",
        "description": (
            "按接口目录调用任意极空间接口。写操作（删除/停止/重启/关机等）需传 confirm_write=true，"
            "调用前请先向用户确认。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "接口路径（如 /zspool/info）或前端函数名"},
                "params": {"type": "object", "description": "业务参数键值对"},
                "confirm_write": {"type": "boolean", "description": "确认执行写操作，默认 false"},
            },
            "required": ["endpoint"],
        },
    },
]


def _text(obj: Any, is_error: bool = False) -> dict[str, Any]:
    body = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def _tool_status(_: dict[str, Any]) -> dict[str, Any]:
    with ZSpaceClient() as client:
        pool = client.pool_info()
        usage = client.disk_usage()
    return _text({"pool": pool.get("data"), "disk_usage": usage.get("data")})


def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 20)
    hits = get_catalog().search(str(args.get("keyword", "")), limit=limit)
    return _text(
        [
            {"method": e.method, "url": e.url, "fn": e.fn, "module": e.module_name, "params": e.params}
            for e in hits
        ]
    )


def _tool_call(args: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(args.get("endpoint", "")).strip()
    if not endpoint:
        return _text("缺少 endpoint 参数", True)
    params = args.get("params") or {}

    catalog = get_catalog()
    matches = catalog.resolve(endpoint)
    probe = (matches[0].url if matches else endpoint).split("?")[0]
    if not _is_read_only(probe) and not args.get("confirm_write"):
        return _text(
            f"接口 {probe} 看起来会修改数据或系统状态（或无法确认为只读），已拦截。"
            "请先向用户确认，确认后带 confirm_write=true 重新调用。",
            True,
        )
    with ZSpaceClient() as client:
        _, data = catalog.call_by_catalog(client, endpoint, params=params, check_code=False)
    return _text(data)


_HANDLERS = {
    "zspace_status": _tool_status,
    "zspace_api_search": _tool_search,
    "zspace_api_call": _tool_call,
}


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 消息；通知类消息返回 None."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # 通知，如 notifications/initialized
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "zspace", "version": __version__},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        handler = _HANDLERS.get(params.get("name"))
        if handler is None:
            return err(-32602, f"未知工具: {params.get('name')}")
        try:
            return ok(handler(params.get("arguments") or {}))
        except Exception as exc:  # 工具内部异常作为工具错误返回，不中断服务
            return ok(_text(f"调用失败: {exc}", True))
    return err(-32601, f"未支持的方法: {method}")


def serve() -> None:
    """在 stdin/stdout 上循环处理请求."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "无效的 JSON"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
