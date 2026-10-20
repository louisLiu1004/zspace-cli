"""极空间 (ZSpace NAS) CLI 主程序入口 (`zs`).

挂载子命令：
- `zs check` / `zs auth`: 连接状态检查、脱敏会话详情与 RSA 加密登录
- `zs api`: 全量 73 模块 / 2,004 接口搜索、参数查询与动态调用引擎
- `zs xl`: 迅雷远程下载管理（自动安全挂载 `xunlei_cmd`）
- `zs dl`: 极空间原生下载中心 PT/BT/HTTP 管理（自动安全挂载 `download_cmd`）
"""

from __future__ import annotations

import typer

from zspace_cli import __version__
from zspace_cli.commands.api_cmd import api_app
from zspace_cli.commands.auth_cmd import auth_app, check_cmd

app = typer.Typer(
    name="zs",
    help="极空间 (ZSpace NAS) 全功能命令行工具 — 支持全量 2,004 接口动态调用、存储监控、迅雷与下载中心管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# 挂载核心子命令组
app.add_typer(auth_app, name="auth")
app.add_typer(api_app, name="api")

# 顶层快捷命令 `zs check`
app.command("check", help="检查极空间连接状态、存储池与磁盘空间概览（等同于 `zs auth check`）")(check_cmd)

# 按表自动挂载领域子命令：(模块名, Typer 对象名, 命令名)
# 模块缺失或导入失败时静默跳过，不影响其它命令组
_SUBCOMMANDS = [
    ("xunlei_cmd", "xl_app", "xl"),
    ("download_cmd", "dl_app", "dl"),
    ("file_cmd", "file_app", "file"),
    ("pool_cmd", "pool_app", "pool"),
    ("safe_cmd", "safe_app", "safe"),
    ("share_cmd", "share_app", "share"),
    ("docker_cmd", "docker_app", "docker"),
    ("docker_cmd", "compose_app", "compose"),
    ("vm_cmd", "vm_app", "vm"),
    ("video_cmd", "video_app", "video"),
    ("video_cmd", "sub_app", "sub"),
    ("album_cmd", "album_app", "album"),
    ("music_cmd", "music_app", "music"),
    ("system_cmd", "sys_app", "sys"),
    ("network_cmd", "net_app", "net"),
    ("smb_cmd", "smb_app", "smb"),
    ("cloud_cmd", "cloud_app", "cloud"),
    ("backup_cmd", "backup_app", "backup"),
    ("backup_cmd", "nvr_app", "nvr"),
    ("mcp_cmd", "mcp_app", "mcp"),
]


def _register_subcommands() -> None:
    import importlib

    for mod_name, attr, cmd_name in _SUBCOMMANDS:
        try:
            mod = importlib.import_module(f"zspace_cli.commands.{mod_name}")
        except ImportError:
            continue
        typer_obj = getattr(mod, attr, None)
        if typer_obj is not None:
            app.add_typer(typer_obj, name=cmd_name)


_register_subcommands()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"zspace-cli (zs) v{__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="显示 zspace-cli 版本号并退出",
    ),
) -> None:
    """极空间 (ZSpace NAS) CLI 主入口."""


if __name__ == "__main__":
    app()
