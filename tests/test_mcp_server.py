"""针对 src/zspace_cli/mcp/server.py MCP 服务的单元测试."""

from __future__ import annotations

from typing import Any

import pytest

from zspace_cli import __version__
from zspace_cli.mcp.server import PROTOCOL_VERSION, handle


def test_mcp_initialize() -> None:
    """验证 initialize 方法返回规范的协议版本与服务端信息."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 1
    assert "result" in resp

    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "zspace"
    assert result["serverInfo"]["version"] == __version__
    assert "tools" in result["capabilities"]


def test_mcp_tools_list() -> None:
    """验证 tools/list 方法暴露全部定义的工具清单."""
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 2

    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "zspace_status" in tool_names
    assert "zspace_api_search" in tool_names
    assert "zspace_api_call" in tool_names

    # 验证每个工具均包含 name, description, inputSchema
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


def test_mcp_unknown_method() -> None:
    """验证调用未知 JSON-RPC 方法返回 -32601 错误码."""
    req = {"jsonrpc": "2.0", "id": 3, "method": "non_existent_method"}
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 3
    assert "error" in resp
    assert resp["error"]["code"] == -32601
    assert "未支持的方法" in resp["error"]["message"]


def test_mcp_unknown_tool() -> None:
    """验证 tools/call 调用不存在的工具返回 -32602 错误码."""
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "undefined_custom_tool", "arguments": {}},
    }
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 4
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "未知工具" in resp["error"]["message"]


def test_mcp_notification_returns_none() -> None:
    """验证没有 id 字段的通知类消息直接返回 None."""
    req = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    assert handle(req) is None


def test_mcp_ping() -> None:
    """验证 ping 心跳响应正常."""
    req = {"jsonrpc": "2.0", "id": 5, "method": "ping"}
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 5
    assert resp["result"] == {}


def test_mcp_tool_search() -> None:
    """验证 zspace_api_search 工具检索功能."""
    req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "zspace_api_search",
            "arguments": {"keyword": "reboot", "limit": 5},
        },
    }
    resp = handle(req)
    assert resp is not None
    assert resp["id"] == 6
    assert resp["result"]["isError"] is False
    content_text = resp["result"]["content"][0]["text"]
    assert "reboot" in content_text.lower()


def test_mcp_api_call_missing_endpoint() -> None:
    """验证 zspace_api_call 缺少 endpoint 时返回明确错误."""
    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "zspace_api_call", "arguments": {}},
    }
    resp = handle(req)
    assert resp is not None
    assert resp["result"]["isError"] is True
    assert "缺少 endpoint 参数" in resp["result"]["content"][0]["text"]


def test_mcp_api_call_write_interception_reboot(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 /system/reboot 在未传 confirm_write 时被安全拦截，且不实例化 ZSpaceClient 发起网络请求."""
    class ExplodingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("网络拦截失败：写接口被拦截前不应实例化 ZSpaceClient！")

    # 用会抛出异常的假客户端替换 ZSpaceClient
    monkeypatch.setattr("zspace_cli.mcp.server.ZSpaceClient", ExplodingClient)

    req = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "zspace_api_call",
            "arguments": {"endpoint": "/system/reboot"},
        },
    }
    resp = handle(req)
    assert resp is not None
    assert resp["result"]["isError"] is True
    text = resp["result"]["content"][0]["text"]
    assert "已拦截" in text
    assert "confirm_write=true" in text


def test_mcp_api_call_write_allowed_with_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证当显式传入 confirm_write=True 时，跳过拦截并进入客户端发起调用."""
    client_instantiated = False

    class DummyClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal client_instantiated
            client_instantiated = True

        def __enter__(self) -> DummyClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("zspace_cli.mcp.server.ZSpaceClient", DummyClient)

    # Mock catalog.call_by_catalog 以免进行真实网络或复杂解析
    monkeypatch.setattr(
        "zspace_cli.core.catalog.ApiCatalog.call_by_catalog",
        lambda self, client, endpoint, **kwargs: (200, {"success": True}),
    )

    req = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "zspace_api_call",
            "arguments": {"endpoint": "/system/reboot", "confirm_write": True},
        },
    }
    resp = handle(req)
    assert resp is not None
    assert client_instantiated is True
    assert resp["result"]["isError"] is False


def test_mcp_api_call_write_interception_container_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证写接口 /zdocker/container_op 在不带 confirm_write 时应被安全拦截，且不实例化 ZSpaceClient 发起网络请求。

    【源码缺陷预警】
    当前 src/zspace_cli/mcp/server.py 的 _WRITE_PATTERN 未覆盖 container_op / op，
    导致该危险容器操作接口（开机/关机/重启/删除容器）绕过了安全拦截机制，直接尝试实例化 ZSpaceClient。
    依任务规范要求，保持测试针对正确安全规格进行断言，在测试报告中汇报源码缺陷。
    """
    class ExplodingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("网络拦截失败：写接口被拦截前不应实例化 ZSpaceClient！")

    monkeypatch.setattr("zspace_cli.mcp.server.ZSpaceClient", ExplodingClient)

    req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "zspace_api_call",
            "arguments": {"endpoint": "/zdocker/container_op"},
        },
    }
    resp = handle(req)
    assert resp is not None
    assert resp["result"]["isError"] is True
    text = resp["result"]["content"][0]["text"]
    assert "已拦截" in text
    assert "confirm_write=true" in text


def test_mcp_status_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 zspace_status 只读工具在离线 mock 下正确返回存储池与磁盘概览."""
    class MockClient:
        def __enter__(self) -> MockClient:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def pool_info(self) -> dict[str, Any]:
            return {"code": 200, "data": {"pool_name": "StoragePool1", "healthy": True}}

        def disk_usage(self) -> dict[str, Any]:
            return {"code": 200, "data": {"total": 4000000000000, "used": 1000000000000}}

    monkeypatch.setattr("zspace_cli.mcp.server.ZSpaceClient", MockClient)

    req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "zspace_status", "arguments": {}},
    }
    resp = handle(req)
    assert resp is not None
    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert "StoragePool1" in text
    assert "4000000000000" in text
