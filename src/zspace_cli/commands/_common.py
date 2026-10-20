"""各领域命令组共用的小工具：统一调用、错误处理、JSON 输出与格式化."""

from __future__ import annotations

import json
import time
from typing import Any

import typer
from rich.console import Console

from zspace_cli.core.client import ZSpaceClient, ZSpaceError

console = Console()


def call(method: str, endpoint: str, **kwargs: Any) -> Any:
    """发起一次请求并返回完整响应；失败时给出友好提示并退出."""
    try:
        with ZSpaceClient() as client:
            return client.request(method, endpoint, check_code=False, **kwargs)
    except ZSpaceError as exc:
        console.print(f"[red]请求失败[/red] {endpoint}: {exc}")
        raise typer.Exit(1)
    except Exception as exc:  # 网络/配置等异常
        console.print(f"[red]请求异常[/red] {endpoint}: {exc}")
        raise typer.Exit(1)


def call_data(method: str, endpoint: str, **kwargs: Any) -> Any:
    """发起请求并返回 `data` 字段；业务 code 非 200 时提示后退出."""
    resp = call(method, endpoint, **kwargs)
    if isinstance(resp, dict) and str(resp.get("code")) not in ("200", "0"):
        console.print(
            f"[yellow]接口 {endpoint} 返回 code={resp.get('code')} msg={resp.get('msg')}[/yellow]"
            "（对应的应用/服务可能未安装或未启动）"
        )
        raise typer.Exit(1)
    return resp.get("data") if isinstance(resp, dict) else resp


def print_json(obj: Any) -> None:
    console.print_json(json.dumps(obj, ensure_ascii=False, default=str))


def human_bytes(n: Any) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(v) < 1024 or unit == "PB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.2f} {unit}"
        v /= 1024
    return "-"


def human_time(ts: Any) -> str:
    try:
        t = int(ts)
        if t <= 0:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except (TypeError, ValueError):
        return "-"


def yes_no(v: Any) -> str:
    return "[green]开启[/green]" if v else "[dim]关闭[/dim]"


def mask(text: Any, keep: int = 3) -> str:
    """脱敏：只保留首尾少量字符."""
    s = str(text or "")
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}***{s[-keep:]}"
