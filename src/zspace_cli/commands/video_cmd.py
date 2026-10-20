"""极空间 `zs video` 极影视（只读）.

已在真机验证返回 200 的接口：
`/zvideo/services/status`、`/zvideo/classification/list`、
`/zvideo/home/collection/latest`、`/zvideo/video/randomlist`。
刮削、字幕搜索等需要参数的接口未验证，可通过 `zs api search zvideo` 查找后用 `zs api call` 调用。
"""

from __future__ import annotations

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import call_data, console, print_json

video_app = typer.Typer(
    name="video",
    help="极影视：服务状态、影视库、最近添加（只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = video_app


@video_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """极影视服务运行状态."""
    d = call_data("GET", "/zvideo/services/status")
    if as_json:
        print_json(d)
        return
    comps = d.get("components") or {}
    console.print(
        f"状态：[bold]{d.get('state')}[/bold]  数据库 {comps.get('database')}  存储 {comps.get('storage')}\n"
        f"启动时间：{d.get('started_at')}  已运行：{d.get('uptime')}"
    )


@video_app.command("libs")
def libs(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """影视库分类及其影片/剧集数量与共享用户数."""
    data = call_data("POST", "/zvideo/classification/list")
    if as_json:
        print_json(data)
        return
    t = Table(title=f"极影视分类库 (共 {len(data)} 个)", box=box.SIMPLE_HEAVY)
    for col in ("名称", "ID", "合集数", "剧集数", "系统分类", "共享人数"):
        t.add_column(col, overflow="fold")
    for c in data:
        t.add_row(
            str(c.get("name")),
            str(c.get("id")),
            str(c.get("collection_count")),
            str(c.get("series_count")),
            "是" if c.get("is_system") else "否",
            str(len(c.get("share_users") or [])),
        )
    console.print(t)


@video_app.command("latest")
def latest(
    limit: int = typer.Option(20, "--limit", "-n", help="最多显示条数"),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    """首页「最近添加」的影视合集."""
    data = call_data("POST", "/zvideo/home/collection/latest")[:limit]
    if as_json:
        print_json(data)
        return
    t = Table(title="最近添加", box=box.SIMPLE_HEAVY)
    for col in ("标题", "分类", "年份", "评分", "类型/地区", "主演"):
        t.add_column(col, overflow="fold")
    for v in data:
        actors = "、".join(a.get("name", "") for a in (v.get("actors") or [])[:3])
        t.add_row(
            str(v.get("title")),
            str(v.get("classification_name")),
            str(v.get("release_year") or "-"),
            str(v.get("score") or "-"),
            f"{v.get('genres') or '-'} / {v.get('regions') or '-'}",
            actors or "-",
        )
    console.print(t)
