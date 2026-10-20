"""极空间 `zs album` 极相册（只读）.

已在真机验证返回 200 的接口：
`/v2/album/home`、`/v2/album/ai/state`、`/v2/album/db/checkStatus`。
自然语言搜图（RAG）等需要参数的接口未验证，可用 `zs api search album` 查找后调用。
"""

from __future__ import annotations

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import call_data, console, human_bytes, print_json

album_app = typer.Typer(
    name="album",
    help="极相册：相册概览与 AI 分析进度（只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = album_app


@album_app.command("home")
def home(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """相册首页：相册列表与「我喜欢」照片."""
    data = call_data("POST", "/v2/album/home")
    if as_json:
        print_json(data)
        return
    albums = (data.get("albums") or {}).get("list") or []
    t = Table(title=f"相册 (共 {(data.get('albums') or {}).get('total', len(albums))} 个)", box=box.SIMPLE_HEAVY)
    for col in ("ID", "名称", "类型"):
        t.add_column(col)
    for a in albums:
        t.add_row(str(a.get("id")), str(a.get("name")), str(a.get("type")))
    console.print(t)

    likes = (data.get("ilike") or {}).get("list") or []
    if likes:
        l = Table(title=f"我喜欢 (共 {(data.get('ilike') or {}).get('total')} 张)", box=box.SIMPLE_HEAVY)
        for col in ("文件名", "大小", "尺寸", "拍摄日期"):
            l.add_column(col, overflow="fold")
        for p in likes:
            l.add_row(str(p.get("name")), human_bytes(p.get("size")), f"{p.get('width')}x{p.get('height')}", str(p.get("cdate")))
        console.print(l)


@album_app.command("ai")
def ai_state(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """AI 分析（人脸/场景/宠物/以图搜图）进度."""
    d = call_data("POST", "/v2/album/ai/state")
    if as_json:
        print_json(d)
        return
    t = Table(title=f"AI 分析状态：{d.get('state')}", box=box.SIMPLE_HEAVY)
    for col in ("项目", "总数"):
        t.add_column(col)
    for label, key in (
        ("人脸（照片）", "face_total"),
        ("人脸（视频）", "face_video_total"),
        ("场景识别", "scene_total"),
        ("语义向量(CLIP)", "clip_total"),
        ("宠物", "pet_total"),
    ):
        t.add_row(label, str(d.get(key)))
    console.print(t)


@album_app.command("db")
def db_status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """相册数据库健康与备份状态."""
    check = call_data("POST", "/v2/album/db/checkStatus")
    backup = call_data("POST", "/v2/album/db/backupInfo")
    if as_json:
        print_json({"check": check, "backup": backup})
        return
    console.print("[bold]数据库检查[/bold]")
    print_json(check)
    console.print("[bold]备份信息[/bold]")
    print_json(backup)
