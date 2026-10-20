"""极空间 `zs file` 文件管理、传输、回收站、压缩解压与空间清理命令组.

涵盖 API 模块：
- 模块 5219：核心文件管理 (`/v2/file/*`)、回收站 (`/v2/file/nb/list`, `/v2/file/restore`, `/v2/file/rclean`)、
  在线解压 (`/v2/file/decompress/make`)、在线压缩 (`/v2/compression/create`)、大文件扫描 (`/v2/file/find/large/*`)
- 模块 85092：全文与索引搜索 (`/file_search/file_search`)
- 模块 15786：重复文件查找与去重 (`/v2/fdd/*`)
- Electron 主进程协议：小文件直传 (`/v2/file/create`)、2MB 分片断点续传 (`/v2/file/tmpinfo` + `/v2/file/upload`)、
  流式下载 (`/v2/file/download`)
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.tree import Tree

from zspace_cli.core.client import ZSpaceClient, ZSpaceError, _urlencode
from zspace_cli.core.crypto import calc_upload_uuid

file_app = typer.Typer(
    name="file",
    help="文件管理、上传下载、全文搜索、回收站、在线解压缩与大文件/重复文件清理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
trash_app = typer.Typer(
    name="trash",
    help="个人与公共回收站查看、还原及清空管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
file_app.add_typer(trash_app, name="trash")

# 导出别名供 main.py 自动发现
app = file_app
console = Console()

DEFAULT_POOL = "sata11"
DEFAULT_SLICE_THRESHOLD = 64 * 1024 * 1024  # 64 MiB
DEFAULT_SLICE_SIZE = 2 * 1024 * 1024        # 2 MiB 分片大小

# 搜索文件类型映射 (模块 5219)
SEARCH_FTYPE_MAP: dict[str, int] = {
    "img": 101,
    "image": 101,
    "video": 102,
    "music": 103,
    "audio": 103,
    "torrent": 104,
    "zip": 106,
    "archive": 106,
    "doc": 107,
    "code": 499,
}


def _as_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def format_size(size_bytes: int | float) -> str:
    """将字节数格式化为易读字符串."""
    val = float(size_bytes or 0)
    if val >= 1024**4:
        return f"{val / (1024**4):.2f} TB"
    if val >= 1024**3:
        return f"{val / (1024**3):.2f} GB"
    if val >= 1024**2:
        return f"{val / (1024**2):.1f} MB"
    if val >= 1024:
        return f"{val / 1024:.1f} KB"
    return f"{int(val)} B"


def parse_size_str(size_str: str) -> int:
    """解析形如 '100M', '2G', '500K' 的容量字符串为字节数."""
    s = size_str.strip().upper()
    if s.endswith("B"):
        s = s[:-1].strip()
    multipliers = {
        "K": 1024,
        "KI": 1024,
        "M": 1024**2,
        "MI": 1024**2,
        "G": 1024**3,
        "GI": 1024**3,
        "T": 1024**4,
        "TI": 1024**4,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            num_part = s[: -len(suffix)].strip()
            return int(float(num_part) * mult)
    return int(float(s))


def format_ts(ts_val: Any) -> str:
    """将秒级或毫秒级时间戳格式化为 YYYY-MM-DD HH:MM:SS."""
    if not ts_val:
        return "-"
    s = str(ts_val).strip()
    if "-" in s and ":" in s:
        return s
    try:
        num = float(s)
        if num > 1e11:
            num /= 1000.0
        if num <= 0:
            return "-"
        return datetime.fromtimestamp(num).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return s


def file_icon(item: dict[str, Any]) -> str:
    """根据目录/文件类型返回终端图标标签."""
    if str(item.get("is_dir")) == "1":
        if item.get("encrypted"):
            return "🔒 [bold yellow]DIR[/bold yellow]"
        return "📁 [bold blue]DIR[/bold blue]"
    ftype = _as_int(item.get("ftype"))
    ext = str(item.get("ext") or Path(str(item.get("name") or "")).suffix).lower().lstrip(".")
    if ftype == 102 or ext in ("mp4", "mkv", "avi", "mov", "ts", "rmvb", "flv", "iso"):
        return "🎬 [magenta]VID[/magenta]"
    if ftype == 101 or ext in ("jpg", "jpeg", "png", "gif", "webp", "heic", "raw"):
        return "🖼️ [green]IMG[/green]"
    if ftype == 103 or ext in ("mp3", "flac", "wav", "aac", "m4a", "ogg", "ape"):
        return "🎵 [cyan]AUD[/cyan]"
    if ftype == 106 or ext in ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"):
        return "📦 [yellow]ZIP[/yellow]"
    if ftype == 107 or ext in ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md"):
        return "📄 [white]DOC[/white]"
    return "📄 [dim]FILE[/dim]"


def resolve_nas_path(client: ZSpaceClient, path: str | None, pool: str = DEFAULT_POOL) -> str:
    """规范化 NAS 路径：若未提供则返回 /<pool>/my/data，若为相对路径则自动拼接."""
    if not path:
        return f"/{pool.strip('/')}/my/data"
    clean = path.strip()
    if clean.startswith("/"):
        return clean
    return f"/{pool.strip('/')}/my/data/{clean}"


def fetch_dir_list(
    client: ZSpaceClient,
    path: str,
    show_hidden: bool = False,
    sort_by: str = "name",
    order: str = "asc",
    limit: int = 200,
) -> tuple[list[dict[str, Any]], int]:
    """分页调用 `/v2/file/list` 获取目录列表（服务端单页上限 50 条）."""
    items: list[dict[str, Any]] = []
    start = 0
    total = 0
    page_size = min(50, limit) if limit > 0 else 50

    while True:
        req_data: dict[str, Any] = {
            "path": path,
            "show_hidden": "1" if show_hidden else "0",
            "start": str(start),
            "limit": str(page_size),
            "num": str(page_size),
        }
        if sort_by:
            req_data["sortby"] = sort_by
        if order:
            req_data["order"] = order

        resp = client.post("/v2/file/list", data=req_data)
        data = resp.get("data") or {}
        page = data.get("list") or []
        total = _as_int(data.get("total")) or (start + len(page))
        items.extend(page)
        start += len(page)
        if not page or (limit > 0 and len(items) >= limit) or start >= total:
            break

    return (items[:limit] if limit > 0 else items), total


# ── 1. 目录浏览与详情命令 ──


@file_app.command("ls")
def ls_cmd(
    path: Optional[str] = typer.Argument(
        None,
        help="NAS 目录路径（默认 /sata11/my/data，支持相对路径如 Media/电影）",
    ),
    pool: str = typer.Option(DEFAULT_POOL, "--pool", "-P", help="默认存储池名称"),
    hidden: bool = typer.Option(False, "--hidden", "-a", help="显示隐藏文件"),
    sort: str = typer.Option("name", "--sort", "-s", help="排序字段: name / size / time / type"),
    order: str = typer.Option("asc", "--order", "-o", help="排序方向: asc / desc"),
    limit: int = typer.Option(200, "--limit", "-n", help="最大返回条目数（0 表示不限制）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出目录内容 (`/v2/file/list`)，支持文件大小、修改时间与类型图标展示."""
    sort_map = {"time": "modify_time", "mtime": "modify_time", "size": "size", "name": "name", "type": "ftype"}
    sort_field = sort_map.get(sort.lower(), sort)

    with ZSpaceClient() as client:
        target_path = resolve_nas_path(client, path, pool=pool)
        try:
            items, total = fetch_dir_list(
                client,
                target_path,
                show_hidden=hidden,
                sort_by=sort_field,
                order=order.lower(),
                limit=limit,
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 获取目录失败:[/red] {exc}")
            raise typer.Exit(1)

        if json_output:
            console.print(
                json.dumps(
                    {"path": target_path, "total": total, "count": len(items), "list": items},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        table = Table(
            title=f"📂 目录列表: [bold cyan]{target_path}[/bold cyan] (共 {total} 项，当前展示 {len(items)} 项)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("类型", width=9)
        table.add_column("名称", style="bold")
        table.add_column("大小", justify="right", width=11)
        table.add_column("修改时间", width=17, style="dim")
        table.add_column("完整路径", style="dim")

        for it in items:
            is_dir = str(it.get("is_dir")) == "1"
            name_str = str(it.get("name") or "")
            display_name = f"[bold blue]{name_str}/[/bold blue]" if is_dir else name_str
            size_str = "-" if is_dir else format_size(_as_int(it.get("size")))
            mtime_str = format_ts(it.get("modify_time"))
            table.add_row(
                file_icon(it),
                display_name,
                size_str,
                mtime_str,
                str(it.get("path") or ""),
            )

        console.print(table)


@file_app.command("tree")
def tree_cmd(
    path: Optional[str] = typer.Argument(None, help="起始目录路径（默认 /sata11/my/data）"),
    depth: int = typer.Option(2, "--depth", "-d", help="最大递归深度（默认 2 层）"),
    pool: str = typer.Option(DEFAULT_POOL, "--pool", "-P", help="默认存储池名称"),
    hidden: bool = typer.Option(False, "--hidden", "-a", help="包含隐藏文件"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出目录树"),
) -> None:
    """递归展示目录树结构."""
    with ZSpaceClient() as client:
        root_path = resolve_nas_path(client, path, pool=pool)
        visited: set[str] = set()

        def _walk_json(curr_path: str, curr_depth: int) -> list[dict[str, Any]]:
            if curr_depth >= depth or curr_path in visited:
                return []
            visited.add(curr_path)
            try:
                entries, _ = fetch_dir_list(client, curr_path, show_hidden=hidden, limit=100)
            except ZSpaceError:
                return []
            nodes: list[dict[str, Any]] = []
            for it in entries:
                is_dir = str(it.get("is_dir")) == "1"
                node: dict[str, Any] = {
                    "name": it.get("name", ""),
                    "path": it.get("path", ""),
                    "is_dir": is_dir,
                    "size": _as_int(it.get("size")),
                }
                if is_dir and curr_depth + 1 < depth:
                    node["children"] = _walk_json(str(it.get("path")), curr_depth + 1)
                nodes.append(node)
            return nodes

        if json_output:
            tree_data = _walk_json(root_path, 0)
            console.print(json.dumps({"root": root_path, "depth": depth, "tree": tree_data}, ensure_ascii=False, indent=2))
            return

        rich_tree = Tree(f"📂 [bold cyan]{root_path}[/bold cyan]")

        def _walk_rich(parent_branch: Tree, curr_path: str, curr_depth: int) -> None:
            if curr_depth >= depth or curr_path in visited:
                return
            visited.add(curr_path)
            try:
                entries, _ = fetch_dir_list(client, curr_path, show_hidden=hidden, limit=100)
            except ZSpaceError as exc:
                parent_branch.add(f"[red]! 无法读取: {exc.msg}[/red]")
                return
            for it in entries:
                is_dir = str(it.get("is_dir")) == "1"
                name = str(it.get("name") or "")
                if is_dir:
                    sub = parent_branch.add(f"📁 [bold blue]{name}/[/bold blue]")
                    if curr_depth + 1 < depth:
                        _walk_rich(sub, str(it.get("path")), curr_depth + 1)
                else:
                    sz = format_size(_as_int(it.get("size")))
                    parent_branch.add(f"📄 {name} [dim]({sz})[/dim]")

        _walk_rich(rich_tree, root_path, 0)
        console.print(rich_tree)


@file_app.command("info")
def info_cmd(
    path: str = typer.Argument(..., help="目标文件或目录路径"),
    pool: str = typer.Option(DEFAULT_POOL, "--pool", "-P", help="默认存储池名称"),
    stat: bool = typer.Option(True, "--stat/--no-stat", help="对目录额外请求 /v2/file/statistic 统计子项数量与总大小"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看文件/目录详细元数据 (`/v2/file/info`) 与目录空间统计 (`/v2/file/statistic`)."""
    with ZSpaceClient() as client:
        target_path = resolve_nas_path(client, path, pool=pool)
        try:
            info_resp = client.post("/v2/file/info", data={"path": target_path})
            info_data = info_resp.get("data") or {}
        except ZSpaceError as exc:
            console.print(f"[red]! 获取文件信息失败:[/red] {exc}")
            raise typer.Exit(1)

        stat_task: dict[str, Any] = {}
        is_dir = str(info_data.get("is_dir")) == "1"
        if stat and is_dir:
            try:
                stat_resp = client._post_with_array(
                    "/v2/file/statistic",
                    [target_path],
                    {"show_hidden": "1"},
                )
                stat_task = (stat_resp.get("data") or {}).get("task") or {}
            except Exception:
                pass

        if json_output:
            console.print(
                json.dumps(
                    {"info": info_data, "statistic": stat_task or None},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column()

        grid.add_row("名称:", f"[bold]{info_data.get('name', '-')}[/bold]")
        grid.add_row("完整路径:", str(info_data.get("path") or target_path))
        grid.add_row("类型:", "目录 (Directory)" if is_dir else f"文件 (ext: {info_data.get('ext') or '-'})")
        if not is_dir:
            sz = _as_int(info_data.get("size"))
            grid.add_row("文件大小:", f"[bold green]{format_size(sz)}[/bold green] ({sz:,} Bytes)")
        grid.add_row("修改时间:", format_ts(info_data.get("modify_time")))
        grid.add_row("变更时间:", format_ts(info_data.get("change_time") or info_data.get("create_time")))
        if info_data.get("file_hash"):
            grid.add_row("特征哈希:", str(info_data.get("file_hash")))

        if stat_task:
            total_sz = _as_int(stat_task.get("size"))
            tfnum = _as_int(stat_task.get("tfnum"))
            tdirnum = _as_int(stat_task.get("tdirnum"))
            st_state = str(stat_task.get("state") or "")
            state_note = "已完成" if st_state == "done" else f"计算中 ({st_state})"
            grid.add_row("递归总大小:", f"[bold green]{format_size(total_sz)}[/bold green] [{state_note}]")
            grid.add_row("包含文件/目录:", f"{tfnum:,} 个文件 / {tdirnum:,} 个子目录")

        console.print(
            Panel(
                grid,
                title=f"[bold]文件/目录详情: {info_data.get('name', target_path)}[/bold]",
                border_style="cyan",
            )
        )


# ── 2. 全文与索引搜索命令 ──


@file_app.command("search")
def search_cmd(
    keyword: str = typer.Argument(..., help="搜索文件名关键词"),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="限定搜索目录前缀（如 /sata11/my/data/Media）"),
    file_type: Optional[str] = typer.Option(
        None,
        "--type",
        "-t",
        help="按类型过滤: video / img / music / doc / zip / code / torrent",
    ),
    dir_only: bool = typer.Option(False, "--dir-only", help="仅搜索文件夹"),
    limit: int = typer.Option(50, "--limit", "-n", help="最大返回条数"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """全局索引搜索文件 (`/file_search/file_search`)，毫秒级检索全盘文件."""
    req_data: dict[str, Any] = {
        "name": keyword,
        "start": 0,
        "num": max(limit, 50),
        "show_hidden": 0,
    }
    if path:
        req_data["file_path"] = path
    if dir_only:
        req_data["is_dir"] = 1
    if file_type and file_type.lower() in SEARCH_FTYPE_MAP:
        req_data["ftype"] = SEARCH_FTYPE_MAP[file_type.lower()]

    with ZSpaceClient() as client:
        try:
            resp = client.post("/file_search/file_search", data=req_data)
        except ZSpaceError as exc:
            console.print(f"[red]! 搜索失败:[/red] {exc}")
            raise typer.Exit(1)

        data = resp.get("data") or {}
        raw_list = data.get("list") or []
        cleaned_list: list[dict[str, Any]] = []
        for item in raw_list:
            row = dict(item)
            # 去除服务端返回的 \x01 和 \x02 高亮控制字符
            if "name" in row and isinstance(row["name"], str):
                row["name"] = row["name"].replace("\x01", "").replace("\x02", "")
            if path:
                prefix = path.rstrip("/")
                p_str = str(row.get("path") or "")
                if p_str != prefix and not p_str.startswith(prefix + "/"):
                    continue
            cleaned_list.append(row)
            if len(cleaned_list) >= limit:
                break

        if json_output:
            console.print(
                json.dumps(
                    {
                        "keyword": keyword,
                        "total": _as_int(data.get("total")),
                        "count": len(cleaned_list),
                        "list": cleaned_list,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not cleaned_list:
            console.print(f"[yellow]未找到包含 '{keyword}' 的文件或目录。[/yellow]")
            return

        table = Table(
            title=f"🔍 文件搜索结果: '{keyword}' (匹配 {len(cleaned_list)} 条 / 总计 {data.get('total', len(cleaned_list))} 条)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("类型", width=9)
        table.add_column("名称", style="bold")
        table.add_column("大小", justify="right", width=11)
        table.add_column("修改时间", width=17, style="dim")
        table.add_column("所在路径", style="green")

        for it in cleaned_list:
            is_dir = str(it.get("is_dir")) == "1"
            sz_str = "-" if is_dir else format_size(_as_int(it.get("size")))
            table.add_row(
                file_icon(it),
                str(it.get("name") or ""),
                sz_str,
                format_ts(it.get("modify_time")),
                str(it.get("path") or ""),
            )
        console.print(table)


# ── 3. 文件增删改查 (mkdir / rename / mv / cp / rm) ──


@file_app.command("mkdir")
def mkdir_cmd(
    parent_or_full_path: str = typer.Argument(..., help="父目录路径（如 /sata11/my/data）或要新建的完整路径"),
    name: Optional[str] = typer.Argument(None, help="新文件夹名称（若第一个参数已是完整路径可省略）"),
    rename_on_conflict: bool = typer.Option(False, "--rename", help="重名时自动重命名"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """新建文件夹 (`/v2/file/newdir`)."""
    if name:
        parent_dir = parent_or_full_path.rstrip("/") or "/"
        folder_name = name.strip("/")
    else:
        cleaned = parent_or_full_path.rstrip("/")
        if "/" not in cleaned or cleaned == "":
            raise typer.BadParameter("请提供完整目录路径或 `父目录 新文件夹名`")
        parent_dir, folder_name = cleaned.rsplit("/", 1)
        if not parent_dir:
            parent_dir = "/"

    with ZSpaceClient() as client:
        try:
            resp = client.post(
                "/v2/file/newdir",
                data={
                    "parent": parent_dir,
                    "name": folder_name,
                    "rename": "1" if rename_on_conflict else "0",
                },
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 新建文件夹失败:[/red] {exc}")
            raise typer.Exit(1)

        if json_output:
            console.print(json.dumps(resp.get("data") or resp, ensure_ascii=False, indent=2))
            return

        created_path = (resp.get("data") or {}).get("path") or f"{parent_dir}/{folder_name}"
        console.print(f"[bold green]✔ 已创建文件夹:[/bold green] {created_path}")


@file_app.command("rename")
def rename_cmd(
    path: str = typer.Argument(..., help="目标文件或目录的完整路径"),
    new_name: str = typer.Argument(..., help="新文件/目录名称（不含路径）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重命名文件或目录 (`/v2/file/modify`)."""
    with ZSpaceClient() as client:
        try:
            resp = client.post("/v2/file/modify", data={"path": path, "newname": new_name})
        except ZSpaceError as exc:
            console.print(f"[red]! 重命名失败:[/red] {exc}")
            raise typer.Exit(1)

        if json_output:
            console.print(json.dumps(resp.get("data") or resp, ensure_ascii=False, indent=2))
            return

        data = resp.get("data") or {}
        console.print(f"[bold green]✔ 已重命名为:[/bold green] {data.get('name', new_name)} ({data.get('path', '')})")


@file_app.command("mv")
def mv_cmd(
    paths: list[str] = typer.Argument(..., help="源文件/目录路径列表，最后一个参数为目标目录路径"),
) -> None:
    """移动文件或目录 (`/v2/file/move`)。用法: `zs file mv <源路径...> <目标目录>`."""
    if len(paths) < 2:
        console.print("[red]! 用法: zs file mv <源路径...> <目标目录>[/red]")
        raise typer.Exit(1)

    sources = list(paths[:-1])
    target_dir = paths[-1]

    with ZSpaceClient() as client:
        try:
            resp = client._post_with_array("/v2/file/move", sources, {"to": target_dir})
        except ZSpaceError as exc:
            console.print(f"[red]! 移动失败:[/red] {exc}")
            raise typer.Exit(1)

        console.print(f"[bold green]✔ 已移动 {len(sources)} 项至:[/bold green] {target_dir}")
        task = (resp.get("data") or {}).get("task")
        if task and task.get("id"):
            console.print(f"  [dim]后台任务 ID: {task['id']} (状态: {task.get('state', 'running')})[/dim]")


@file_app.command("cp")
def cp_cmd(
    paths: list[str] = typer.Argument(..., help="源文件/目录路径列表，最后一个参数为目标目录路径"),
) -> None:
    """复制文件或目录 (`/v2/file/copy`)。用法: `zs file cp <源路径...> <目标目录>`."""
    if len(paths) < 2:
        console.print("[red]! 用法: zs file cp <源路径...> <目标目录>[/red]")
        raise typer.Exit(1)

    sources = list(paths[:-1])
    target_dir = paths[-1]

    with ZSpaceClient() as client:
        try:
            resp = client._post_with_array("/v2/file/copy", sources, {"to": target_dir})
        except ZSpaceError as exc:
            console.print(f"[red]! 复制失败:[/red] {exc}")
            raise typer.Exit(1)

        console.print(f"[bold green]✔ 已复制 {len(sources)} 项至:[/bold green] {target_dir}")
        task = (resp.get("data") or {}).get("task")
        if task and task.get("id"):
            console.print(f"  [dim]后台任务 ID: {task['id']} (状态: {task.get('state', 'running')})[/dim]")


@file_app.command("rm")
def rm_cmd(
    paths: list[str] = typer.Argument(..., help="要删除的一个或多个文件/目录路径"),
    permanent: bool = typer.Option(False, "--permanent", help="跳过回收站直接永久删除 (skip_bin=1)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="免确认直接执行"),
) -> None:
    """删除文件或目录（默认移入个人回收站 `/v2/file/remove`，支持 `--permanent` 彻底删除）."""
    if permanent and not yes:
        confirm = typer.confirm(f"确定要永久删除这 {len(paths)} 个路径吗？此操作不可恢复！")
        if not confirm:
            console.print("[yellow]已取消删除。[/yellow]")
            raise typer.Exit(0)

    extra: dict[str, str] = {"show_hidden": "1"}
    if permanent:
        extra["skip_bin"] = "1"

    with ZSpaceClient() as client:
        try:
            client._post_with_array("/v2/file/remove", list(paths), extra)
        except ZSpaceError as exc:
            console.print(f"[red]! 删除失败:[/red] {exc}")
            raise typer.Exit(1)

        mode_str = "永久删除" if permanent else "移入回收站"
        console.print(f"[bold green]✔ 已{mode_str} {len(paths)} 项:[/bold green] {', '.join(paths)}")


# ── 4. 回收站管理 (`zs file trash`) ──


@trash_app.command("ls")
def trash_ls_cmd(
    public: bool = typer.Option(False, "--public", help="查看公共/共享空间回收站（默认查看个人回收站）"),
    limit: int = typer.Option(100, "--limit", "-n", help="最大返回条数"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看回收站文件列表 (`/v2/file/nb/list`)."""
    bin_path = "/.public_recycle" if public else "/.recycle/my"
    with ZSpaceClient() as client:
        try:
            resp = client.post(
                "/v2/file/nb/list",
                data={"path": bin_path, "start": 0, "num": limit, "show_hidden": 1},
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 获取回收站列表失败:[/red] {exc}")
            raise typer.Exit(1)

        data = resp.get("data") or {}
        items = data.get("list") or []
        total = _as_int(data.get("total")) or len(items)

        if json_output:
            console.print(
                json.dumps(
                    {"bin_path": bin_path, "total": total, "list": items},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not items:
            console.print(f"[green]🎉 {'公共' if public else '个人'}回收站为空。[/green]")
            return

        table = Table(
            title=f"🗑️ {'公共' if public else '个人'}回收站 ({bin_path}) - 共 {total} 项",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("类型", width=9)
        table.add_column("文件名", style="bold")
        table.add_column("大小", justify="right", width=11)
        table.add_column("删除时间", width=17, style="dim")
        table.add_column("回收站内路径 (用于 restore)", style="yellow")
        table.add_column("原始路径", style="dim")

        for it in items:
            is_dir = str(it.get("is_dir")) == "1"
            table.add_row(
                file_icon(it),
                str(it.get("name") or ""),
                "-" if is_dir else format_size(_as_int(it.get("size"))),
                format_ts(it.get("modify_time")),
                str(it.get("path") or ""),
                str(it.get("original_path") or "-"),
            )
        console.print(table)


@trash_app.command("restore")
def trash_restore_cmd(
    paths: list[str] = typer.Argument(..., help="回收站内的文件路径（如 /sata11/.recycle/my/xxx）"),
    public: bool = typer.Option(False, "--public", help="从公共回收站还原 (/v2/public/recycle/restore)"),
) -> None:
    """从回收站还原文件或目录 (`/v2/file/restore`)."""
    endpoint = "/v2/public/recycle/restore" if public else "/v2/file/restore"
    restored = 0
    with ZSpaceClient() as client:
        for p in paths:
            try:
                client.post(endpoint, data={"path": p})
                restored += 1
                console.print(f"[green]✔ 已还原:[/green] {p}")
            except ZSpaceError as exc:
                console.print(f"[red]✘ 还原失败 ({p}):[/red] {exc}")

    if restored == 0:
        raise typer.Exit(1)


@trash_app.command("clean")
def trash_clean_cmd(
    public: bool = typer.Option(False, "--public", help="清空公共回收站 (/v2/public/recycle/clean)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="免确认直接清空"),
) -> None:
    """清空个人或公共回收站 (`/v2/file/rclean` / `/v2/public/recycle/clean`)."""
    target_label = "公共回收站" if public else "个人回收站"
    if not yes:
        if not typer.confirm(f"确定要彻底清空{target_label}吗？此操作不可逆！"):
            console.print("[yellow]已取消操作。[/yellow]")
            raise typer.Exit(0)

    endpoint = "/v2/public/recycle/clean" if public else "/v2/file/rclean"
    with ZSpaceClient() as client:
        try:
            resp = client.post(endpoint)
        except ZSpaceError as exc:
            console.print(f"[red]! 清空回收站失败:[/red] {exc}")
            raise typer.Exit(1)

        task = (resp.get("data") or {}).get("task") or {}
        console.print(
            f"[bold green]✔ 已触发清空{target_label}！[/bold green] "
            f"(清理项数: {task.get('total_num', '-')}, 释放空间: {format_size(_as_int(task.get('total_size')))})"
        )


# ── 5. 文件上传 (`zs file upload`) 与流式下载 (`zs file download`) ──


def _build_upload_slice_cookie(params: dict[str, Any]) -> str:
    """按照逆向规范构造 /v2/file/upload 分片上传的镜像 Cookie 头（nasid 键名转为 nas_id）."""
    pairs: list[str] = []
    for k, v in params.items():
        cookie_key = "nas_id" if k == "nasid" else k
        pairs.append(f"{cookie_key}={v}")
    return "; ".join(pairs)


@file_app.command("upload")
def upload_cmd(
    local_path: Path = typer.Argument(..., exists=True, dir_okay=False, help="本地待上传文件路径"),
    remote_dir: str = typer.Argument(..., help="NAS 目标目录路径（如 /sata11/my/data/文档）"),
    new_name: Optional[str] = typer.Option(None, "--name", "-n", help="上传后的目标文件名（默认保持原文件名）"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="大文件分片上传时启用断点续传查询 (/v2/file/tmpinfo)"),
    force_slice: bool = typer.Option(False, "--slice", help="强制使用 2MB 分片上传协议 (/v2/file/upload)"),
) -> None:
    """上传本地文件到极空间（支持小文件 `/v2/file/create` 直传与大文件 2MB 分片断点续传 `/v2/file/upload`）."""
    local_file = local_path.resolve()
    file_size = local_file.stat().st_size
    target_filename = new_name or local_file.name
    target_path = f"{remote_dir.rstrip('/')}/{target_filename}"

    with ZSpaceClient(timeout=120.0) as client:
        cfg = client.config

        with Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(f"上传 {target_filename}", total=file_size)

            def _upload_via_slices() -> dict[str, Any]:
                st = local_file.stat()
                mtime_ms = math.ceil(st.st_mtime * 1000)
                session_uuid = calc_upload_uuid(mtime_ms, file_size, target_path)
                modify_time_sec = math.ceil(mtime_ms / 1000)

                # 1. 查询断点续传偏移量 (/v2/file/tmpinfo)
                offset = 0
                if resume and file_size > 0:
                    try:
                        tmp_resp = client.get(
                            "/v2/file/tmpinfo",
                            params={"path": target_path, "uuid": session_uuid},
                            check_code=False,
                        )
                        if str(tmp_resp.get("code")) == "200":
                            reported = _as_int((tmp_resp.get("data") or {}).get("size"))
                            if 0 < reported < file_size:
                                offset = reported
                                progress.update(task_id, completed=offset)
                    except Exception:
                        offset = 0

                # 2. 按 2MB 分片严格单调递增上传 (/v2/file/upload)
                with local_file.open("rb") as fh:
                    if offset > 0:
                        fh.seek(offset)
                    while offset < file_size or (file_size == 0 and offset == 0):
                        chunk = fh.read(DEFAULT_SLICE_SIZE)
                        chunk_len = len(chunk)
                        if file_size > 0 and chunk_len == 0:
                            break

                        slice_params: dict[str, Any] = {
                            "app": "file",
                            "path": _urlencode(target_path),
                            "size": file_size,
                            "uuid": session_uuid,
                            "seek": offset,
                            "crtime": "",
                            "modify_time": modify_time_sec,
                            "rename": 0,
                            "token": _urlencode(cfg.token),
                            "plat": "pc",
                            "nasid": cfg.nas_id,
                            "version": cfg.app_version or "1.0",
                            "device_id": cfg.device_id,
                            "device": _urlencode(cfg.device),
                            "Content-Length": chunk_len,
                            "request-purpose": 4,
                            "remote-port": 8050,
                            "split": 1,
                        }
                        headers = {k: str(v) for k, v in slice_params.items()}
                        headers["Cookie"] = _build_upload_slice_cookie(slice_params)
                        headers["Content-Type"] = "application/octet-stream"

                        drnd = int(time.time() * 1000)
                        upload_url = client._resolve_target_url(
                            f"/v2/file/upload?remote_port=8050&drnd={drnd}&uuid={session_uuid}",
                            remote_port=8050,
                        )
                        resp = client._http.post(upload_url, content=chunk, headers=headers)
                        client._parse_and_validate(resp, endpoint="/v2/file/upload", check_code=True)

                        offset += chunk_len
                        progress.update(task_id, completed=offset)
                        if file_size == 0:
                            break

                return {"path": target_path, "size": file_size, "mode": "sliced"}

            try:
                if force_slice or file_size > DEFAULT_SLICE_THRESHOLD:
                    res = _upload_via_slices()
                else:
                    # 小文件尝试单次 /v2/file/create 上传
                    create_url = client._append_rnd_to_url(
                        client._resolve_target_url("/v2/file/create")
                    )
                    raw_bytes = local_file.read_bytes()
                    # 注意：httpx 要求含中文的 Header 值编码为 UTF-8 bytes 或使用 latin-1 兼容，
                    # 但极空间本地代理直接接收 UTF-8 字节头，若报编码异常则自动回退分片上传
                    try:
                        headers = {
                            "Cookie": client._build_cookie_header(),
                            "Content-Type": "application/octet-stream",
                            "path": target_path.encode("utf-8"),  # type: ignore[dict-item]
                        }
                        resp = client._http.post(create_url, content=raw_bytes, headers=headers)
                        if resp.status_code == 413:
                            res = _upload_via_slices()
                        else:
                            client._parse_and_validate(resp, endpoint="/v2/file/create", check_code=True)
                            progress.update(task_id, completed=file_size)
                            res = {"path": target_path, "size": file_size, "mode": "create"}
                    except (UnicodeEncodeError, httpx.HTTPError):
                        res = _upload_via_slices()
            except ZSpaceError as exc:
                console.print(f"[red]! 上传失败:[/red] {exc}")
                raise typer.Exit(1)

        console.print(
            f"[bold green]✔ 上传完成！[/bold green] {res['path']} "
            f"({format_size(file_size)}, 模式: {res['mode']})"
        )


@file_app.command("download")
def download_cmd(
    remote_path: str = typer.Argument(..., help="NAS 上的远端文件路径（如 /sata11/my/data/test.txt）"),
    local_path: Optional[Path] = typer.Argument(
        None,
        help="本地保存目录或完整文件路径（默认保存到当前工作目录）",
    ),
) -> None:
    """从极空间流式下载文件 (`/v2/file/download?remote_port=8050`)，带进度条与错误 JSON 识别."""
    remote_filename = Path(remote_path).name
    if local_path is None:
        dest_file = Path.cwd() / remote_filename
    elif local_path.is_dir():
        dest_file = local_path / remote_filename
    else:
        dest_file = local_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

    with ZSpaceClient(timeout=120.0) as client:
        dl_url = client._append_rnd_to_url(
            client._resolve_target_url("/v2/file/download", remote_port=8050)
        )
        headers = {
            "Cookie": client._build_cookie_header(),
            "remote-port": "8050",
        }
        params = {"path": remote_path, "remote_port": "8050"}

        try:
            with client._http.stream("GET", dl_url, params=params, headers=headers) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                # 极空间在文件不存在 (N001315) 或目标是目录 (N001533) 时仍返回 HTTP 200，
                # 但 Content-Type 为 application/json，需优先拦截避免把错误 JSON 写入磁盘
                if ctype.startswith("application/json"):
                    raw_err = resp.read()
                    err_obj = json.loads(raw_err.decode("utf-8", errors="ignore"))
                    if isinstance(err_obj, dict) and str(err_obj.get("code")) != "200":
                        raise ZSpaceError(
                            err_obj.get("code", "ERR"),
                            err_obj.get("msg") or err_obj.get("message") or "下载失败",
                            endpoint="/v2/file/download",
                        )

                total_bytes = _as_int(resp.headers.get("content-length"))
                with Progress(
                    TextColumn("[bold green]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    t_id = progress.add_task(f"下载 {remote_filename}", total=total_bytes or None)
                    downloaded = 0
                    with dest_file.open("wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                            fh.write(chunk)
                            downloaded += len(chunk)
                            progress.update(t_id, completed=downloaded)
        except ZSpaceError as exc:
            dest_file.unlink(missing_ok=True)
            console.print(f"[red]! 下载失败:[/red] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            dest_file.unlink(missing_ok=True)
            console.print(f"[red]! 下载异常:[/red] {exc}")
            raise typer.Exit(1)

    console.print(
        f"[bold green]✔ 下载完成:[/bold green] {dest_file} ({format_size(dest_file.stat().st_size)})"
    )


# ── 6. 在线解压 (`zs file unzip`) 与在线压缩 (`zs file zip`) ──


@file_app.command("unzip")
def unzip_cmd(
    archive_path: str = typer.Argument(..., help="NAS 上的压缩包完整路径（如 /sata11/my/data/test.zip）"),
    target_dir: Optional[str] = typer.Argument(
        None,
        help="解压目标目录路径（默认解压到压缩包所在同级目录）",
    ),
    charset: Optional[str] = typer.Option(None, "--charset", help="指定文件名字符集（如 GBK / UTF-8）"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="解压密码（若有）"),
) -> None:
    """在极空间服务端在线解压压缩包 (`/v2/file/decompress/make`)."""
    dest_dir = target_dir or archive_path.rsplit("/", 1)[0] or "/sata11/my/data"
    req_data: dict[str, Any] = {
        "paths[]": [archive_path],
        "to": dest_dir,
    }
    if charset:
        req_data["charset"] = charset

    with ZSpaceClient() as client:
        try:
            resp = client.post("/v2/file/decompress/make", data=req_data)
            task = (resp.get("data") or {}).get("task") or {}
            task_id = task.get("id")
            if password and task_id:
                client.post(
                    "/v2/file/decompress/setpwd",
                    data={"id": task_id, "pwd": password, "index": 0, "forall": 1, "skip": 0},
                )
        except ZSpaceError as exc:
            console.print(f"[red]! 创建解压任务失败:[/red] {exc}")
            raise typer.Exit(1)

        console.print(
            f"[bold green]✔ 已提交在线解压任务！[/bold green]\n"
            f"  源文件: {archive_path}\n"
            f"  目标目录: {dest_dir}\n"
            f"  任务 ID: {task.get('id', '-')} (状态: {task.get('state', 'running')})"
        )


@file_app.command("zip")
def zip_cmd(
    paths: list[str] = typer.Argument(..., help="要压缩的一个或多个 NAS 路径（最后一个参数若加 --to 则全部为源路径）"),
    to_dir: Optional[str] = typer.Option(None, "--to", "-t", help="输出目录路径（默认保存在第一个源文件的同级目录）"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="设置 4~16 位字母数字压缩密码"),
    level: Optional[str] = typer.Option(None, "--level", "-l", help="压缩级别（如 normal / fast / store）"),
) -> None:
    """在极空间服务端创建在线 ZIP 压缩任务 (`/v2/compression/create`)."""
    if not paths:
        console.print("[red]! 请指定至少一个待压缩路径。[/red]")
        raise typer.Exit(1)

    dest_dir = to_dir or paths[0].rsplit("/", 1)[0] or "/sata11/my/data"
    req_data: dict[str, Any] = {
        "paths[]": list(paths),
        "to": dest_dir,
    }
    if password:
        req_data["pwd"] = password
    if level:
        req_data["compress_level"] = level

    with ZSpaceClient() as client:
        try:
            resp = client.post("/v2/compression/create", data=req_data)
        except ZSpaceError as exc:
            console.print(f"[red]! 创建压缩任务失败:[/red] {exc}")
            raise typer.Exit(1)

        data = resp.get("data") or {}
        task = data.get("task") or {}
        console.print(
            f"[bold green]✔ 已创建在线压缩任务！[/bold green]\n"
            f"  源路径数: {len(paths)} 项\n"
            f"  输出目录: {dest_dir}\n"
            f"  任务模式: {data.get('otype', 'async')} (任务 ID: {task.get('id', '-')})"
        )


# ── 7. 大文件扫描 (`zs file large`) 与重复文件去重 (`zs file dedup`) ──


@file_app.command("large")
def large_cmd(
    path: str = typer.Argument("/sata11/my/data", help="要扫描大文件的起始目录路径"),
    min_size: str = typer.Option("100M", "--min-size", "-m", help="最小文件大小阈值（如 50M, 100M, 1G, 5G）"),
    topk: int = typer.Option(50, "--topk", "-n", help="返回前 N 个最大文件"),
    timeout: float = typer.Option(60.0, "--timeout", help="等待服务端扫描完成的最大秒数"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """服务端高速扫描大文件 (`/v2/file/find/large/*`)."""
    min_bytes = parse_size_str(min_size)
    max_bytes = 1024**5  # 1 PiB

    with ZSpaceClient() as client:
        # 1. 先清理旧任务（忽略 N001307 任务不存在错误）
        try:
            client.post("/v2/file/find/large/delete", check_code=False)
        except Exception:
            pass

        # 2. 创建新扫描任务
        try:
            create_resp = client._post_with_array(
                "/v2/file/find/large/create",
                [path],
                {"min_size": str(min_bytes), "max_size": str(max_bytes), "topk": str(topk)},
            )
            scan_data = create_resp.get("data") or {}
        except ZSpaceError as exc:
            console.print(f"[red]! 启动大文件扫描失败:[/red] {exc}")
            raise typer.Exit(1)

        # 3. 轮询直到 state != 1 (1=扫描中, 2=完成)
        deadline = time.time() + timeout
        with console.status(f"[cyan]正在扫描 {path} 中大于 {min_size} 的文件...[/cyan]"):
            while _as_int(scan_data.get("state")) == 1 and time.time() < deadline:
                time.sleep(1.0)
                info_resp = client.post("/v2/file/find/large/info", check_code=False)
                scan_data = info_resp.get("data") or scan_data

        files = scan_data.get("list") or []
        scanned_cnt = _as_int(scan_data.get("scan_count"))
        match_raw = scan_data.get("match_count")
        matched_cnt = len(match_raw) if isinstance(match_raw, list) else _as_int(match_raw)

        if json_output:
            console.print(
                json.dumps(
                    {
                        "path": path,
                        "min_size": min_bytes,
                        "scanned": scanned_cnt,
                        "matched": matched_cnt,
                        "files": files[:topk],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not files:
            console.print(
                f"[green]扫描完成（共扫描 {scanned_cnt:,} 个文件），未发现大于 {min_size} 的文件。[/green]"
            )
            return

        table = Table(
            title=f"🐘 大文件扫描结果 (>={min_size}) - 已扫描 {scanned_cnt:,} 项，命中 {matched_cnt} 项",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("#", justify="right", width=4, style="dim")
        table.add_column("大小", justify="right", style="bold green", width=12)
        table.add_column("文件名", style="bold")
        table.add_column("修改时间", width=17, style="dim")
        table.add_column("完整路径", style="dim")

        for idx, f in enumerate(files[:topk], start=1):
            table.add_row(
                str(idx),
                format_size(_as_int(f.get("size"))),
                str(f.get("name") or ""),
                format_ts(f.get("modify_time")),
                str(f.get("path") or ""),
            )
        console.print(table)


@file_app.command("dedup")
def dedup_cmd(
    action: str = typer.Argument(
        "status",
        help="操作类型: status (查看去重状态与结果) / scan (启动扫描) / group (查看指定重复组详情)",
    ),
    path: str = typer.Option("/sata11/my/data", "--path", "-p", help="scan 时的扫描目录路径"),
    group_id: Optional[str] = typer.Option(None, "--group-id", "-g", help="group 操作时的重复组 ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="展示的重复文件组数量"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重复文件查找与去重管理 (`/v2/fdd/*`)."""
    act = action.strip().lower()
    with ZSpaceClient() as client:
        if act == "scan":
            try:
                resp = client._post_with_array(
                    "/v2/fdd/create",
                    [path],
                    {"stype": "file", "force": "1", "include_bdmv": "0", "include_sync": "1"},
                )
            except ZSpaceError as exc:
                console.print(f"[red]! 启动重复文件扫描失败:[/red] {exc}")
                raise typer.Exit(1)
            if json_output:
                console.print(json.dumps(resp, ensure_ascii=False, indent=2))
                return
            console.print(f"[bold green]✔ 已启动重复文件扫描任务:[/bold green] {path}")
            console.print("  [dim]提示: 可运行 `zs file dedup status` 查看扫描进度与重复文件组。[/dim]")
            return

        if act == "group":
            if not group_id:
                console.print("[red]! 查看重复组详情需指定 --group-id / -g 参数。[/red]")
                raise typer.Exit(1)
            resp = client.post("/v2/fdd/glist", data={"id": group_id, "start": 0, "num": 50})
            items = resp.get("data") or []
            if json_output:
                console.print(json.dumps(items, ensure_ascii=False, indent=2))
                return
            table = Table(title=f"重复文件组 #{group_id} 详情", box=box.ROUNDED, header_style="bold cyan")
            table.add_column("ID", style="yellow")
            table.add_column("文件名", style="bold")
            table.add_column("大小", justify="right", style="green")
            table.add_column("修改时间", style="dim")
            table.add_column("路径")
            for it in items if isinstance(items, list) else []:
                table.add_row(
                    str(it.get("id", "")),
                    str(it.get("name", "")),
                    format_size(_as_int(it.get("size"))),
                    format_ts(it.get("modify_time")),
                    str(it.get("path", "")),
                )
            console.print(table)
            return

        # 默认 status / ls：查询 /v2/fdd/state，若有结果则拉取 /v2/fdd/list
        state_resp = client.post("/v2/fdd/state", check_code=False)
        state_data = state_resp.get("data") or {}
        gnum = _as_int(state_data.get("gnum"))
        fnum = _as_int(state_data.get("fnum"))
        total_dup_size = _as_int(state_data.get("size"))
        scan_state = _as_int(state_data.get("state"))

        groups: list[dict[str, Any]] = []
        if gnum > 0:
            try:
                list_resp = client.post("/v2/fdd/list", data={"start": 0, "num": limit, "ftype": ""}, check_code=False)
                groups = (list_resp.get("data") or {}).get("items") or []
            except Exception:
                pass

        if json_output:
            console.print(
                json.dumps(
                    {"state": state_data, "groups": groups},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        state_map = {
            0: "未扫描 / 空闲",
            1: "准备中",
            2: "正在扫描中...",
            3: "扫描完成",
            4: "已清理",
            10: "正在清理中...",
        }
        console.print(
            Panel(
                f"任务状态: [bold cyan]{state_map.get(scan_state, f'状态 {scan_state}')}[/bold cyan]  |  "
                f"重复组数: [bold yellow]{gnum}[/bold yellow] 组  |  "
                f"重复文件数: [bold]{fnum}[/bold] 个  |  "
                f"可释放冗余空间: [bold green]{format_size(total_dup_size)}[/bold green]",
                title="[bold]极空间重复文件去重扫描状态 (/v2/fdd/state)[/bold]",
                border_style="cyan",
            )
        )

        if groups:
            table = Table(title=f"重复文件组列表 (前 {len(groups)} 组)", box=box.ROUNDED, header_style="bold cyan")
            table.add_column("组 ID", style="bold yellow")
            table.add_column("文件名示例", style="bold")
            table.add_column("单文件大小", justify="right", style="green")
            table.add_column("重复份数", justify="right")
            for g in groups:
                table.add_row(
                    str(g.get("id", "")),
                    str(g.get("name", "")),
                    format_size(_as_int(g.get("size"))),
                    str(g.get("fnum") or g.get("count") or "-"),
                )
            console.print(table)
        else:
            console.print("[dim]提示: 使用 `zs file dedup scan --path /sata11/my/data` 可启动新的重复文件扫描。[/dim]")
