"""极空间 `zs music` 极音乐（只读）.

探测结论：
当前设备应用商店中「极音乐」套件未安装（`/AppStore/v1/list` 返回 `app_name="music"`, `installed=0`），
后台无音乐服务进程，反向代理路由 `/zmusic/*` 与 `/zaudio/*` 均返回 HTTP 502 Bad Gateway。
根据只读与真机验证约束，不在此封装未安装的业务命令。
"""

from __future__ import annotations

import typer
from rich.panel import Panel

from zspace_cli.commands._common import console

music_app = typer.Typer(
    name="music",
    help="极音乐：音乐库与播放管理（本机未安装此套件）",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
app = music_app


@music_app.callback(invoke_without_command=True)
def music_callback(ctx: typer.Context) -> None:
    """极音乐主命令入口."""
    if ctx.invoked_subcommand is None:
        console.print(
            Panel(
                "[yellow]当前极空间 NAS 设备未安装「极音乐」套件 (app_name: music)[/yellow]\n\n"
                "• 应用商店状态：[dim]installed=0 (未安装)[/dim]\n"
                "• 核心接口探测：[dim]/zmusic/api/v2/* 与 /zaudio/api/v1/* 均返回 HTTP 502 (服务未运行)[/dim]\n"
                "• 如需使用，请先在极空间客户端或应用中心安装「极音乐」套件。",
                title="极音乐 (ZMusic) 状态提示",
                border_style="yellow",
            )
        )
