"""极空间内嵌迅雷远程下载管理命令组 (`zs xl`).

提供以下命令：
- `zs xl ls [--all | --completed | --running] [--json]`：展示迅雷任务列表
- `zs xl info <task_id>`：查看单个迅雷任务的详细参数与 P2P/VIP/超级会员加速状态
- `zs xl parse <url>`：解析磁力链接/直链/电驴链接，展示包含的文件列表、大小与索引号
- `zs xl add <url> [--dir <folder_id>] [--index <sub_file_index>]`：一键解析并添加下载任务
- `zs xl pause <task_id>`：暂停迅雷任务
- `zs xl resume <task_id>`：恢复迅雷任务
- `zs xl rm <task_id> [--delete-file]`：删除迅雷任务
- `zs xl dirs`：查看迅雷下载目录与存储挂载文件夹
- `zs xl status`：查看迅雷客户端状态、版本、会员信息、默认下载目录与空间信息
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.clients.xunlei import (
    PHASE_COMPLETE,
    PHASE_ERROR,
    PHASE_PAUSED,
    PHASE_PENDING,
    PHASE_RUNNING,
    XunleiClient,
    XunleiError,
    simplify_xunlei_path,
)
from zspace_cli.core.client import ZSpaceError

xl_app = typer.Typer(
    name="xl",
    help="极空间内嵌迅雷全功能控制（任务列表、磁力/直链解析、添加下载、暂停/恢复/删除、目录与状态）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
# 导出别名供 main.py 自动识别挂载
app = xl_app
xunlei_app = xl_app

console = Console(width=130 if not sys.stdout.isatty() else None)

# 迅雷任务阶段样式与中文描述映射
PHASE_DISPLAY: dict[str, tuple[str, str]] = {
    PHASE_RUNNING: ("下载中", "bold cyan"),
    PHASE_PENDING: ("等待中", "yellow"),
    PHASE_PAUSED: ("已暂停", "dim yellow"),
    PHASE_COMPLETE: ("已完成", "bold green"),
    PHASE_ERROR: ("异常/失败", "bold red"),
}


def _format_bytes(num_bytes: int | float | str | None) -> str:
    """将字节数格式化为人类可读的容量字符串."""
    try:
        val = float(num_bytes or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val >= 1024**4:
        return f"{val / (1024**4):.2f} TB"
    if val >= 1024**3:
        return f"{val / (1024**3):.2f} GB"
    if val >= 1024**2:
        return f"{val / (1024**2):.1f} MB"
    if val >= 1024:
        return f"{val / 1024:.1f} KB"
    return f"{int(val)} B"


def _format_speed(speed_bps: int | float | str | None) -> str:
    """将字节每秒的速度格式化为易读速度字符串."""
    try:
        val = float(speed_bps or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val <= 0:
        return "-"
    return f"{_format_bytes(val)}/s"


def _format_phase(phase: str, message: str = "") -> str:
    """将迅雷 phase 转换为带 Rich 颜色标签的中文状态."""
    label, style = PHASE_DISPLAY.get(phase, (phase or "未知", "white"))
    if phase == PHASE_ERROR and message:
        return f"[{style}]{label} ({message})[/{style}]"
    return f"[{style}]{label}[/{style}]"


def _extract_save_path(task: dict[str, Any]) -> str:
    """提取并简化任务的保存路径（如 `/data/迅雷下载/...`）."""
    params = task.get("params") or {}
    raw_path = str(params.get("real_path") or params.get("parent_folder_path") or "")
    return simplify_xunlei_path(raw_path) or "-"


@xl_app.command("ls")
def xl_list_cmd(
    all_tasks: bool = typer.Option(True, "--all", help="显示全部任务（含下载中与已完成，默认开启）"),
    completed: bool = typer.Option(False, "--completed", "-c", help="仅显示已完成任务"),
    running: bool = typer.Option(False, "--running", "-r", help="仅显示进行中/未完成任务"),
    limit: int = typer.Option(20, "--limit", "-n", help="最大返回任务数量"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出原始任务列表"),
) -> None:
    """展示极空间迅雷下载任务列表（ID、名称、大小、进度、当前速度、状态、保存路径）."""
    status_filter = "all"
    if completed and not running:
        status_filter = "completed"
    elif running and not completed:
        status_filter = "running"
    elif all_tasks:
        status_filter = "all"

    try:
        with XunleiClient() as xl:
            tasks = xl.list_tasks(status=status_filter, limit=limit)
    except ZSpaceError as exc:
        console.print(f"[bold red]获取迅雷任务列表失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(tasks, ensure_ascii=False))
        return

    title_suffix = {
        "all": "全部任务",
        "completed": "已完成任务",
        "running": "进行中任务",
    }.get(status_filter, "全部任务")

    table = Table(
        title=f"极空间迅雷任务列表 — {title_suffix} (共 {len(tasks)} 条)",
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column("任务 ID", style="cyan", no_wrap=True)
    table.add_column("名称", style="bold white", overflow="fold", min_width=20, ratio=2)
    table.add_column("大小", justify="right", style="magenta", no_wrap=True)
    table.add_column("进度", justify="right", no_wrap=True)
    table.add_column("当前速度", justify="right", style="green", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("保存路径", style="dim", overflow="fold", min_width=22, ratio=2)

    for t in tasks:
        params = t.get("params") or {}
        tid = str(t.get("id") or "")
        name = str(t.get("name") or t.get("file_name") or "-")
        size_str = _format_bytes(t.get("file_size"))
        progress = int(t.get("progress") or 0)
        prog_str = f"[green]{progress}%[/green]" if progress >= 100 else f"[yellow]{progress}%[/yellow]"

        base_speed = int(params.get("speed") or 0)
        speedup_speed = int(params.get("speedup_speed") or 0)
        total_speed = base_speed + speedup_speed
        speed_str = _format_speed(total_speed)
        if speedup_speed > 0:
            speed_str += f" (含加速 {_format_speed(speedup_speed)})"

        phase_str = _format_phase(str(t.get("phase") or ""), str(t.get("message") or ""))
        save_path = _extract_save_path(t)

        table.add_row(tid, name, size_str, prog_str, speed_str, phase_str, save_path)

    console.print(table)


@xl_app.command("info")
def xl_info_cmd(
    task_id: str = typer.Argument(..., help="迅雷任务 ID（可通过 `zs xl ls` 获取）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出完整任务结构体"),
) -> None:
    """查看单个迅雷任务的详细参数与加速状态（P2P/VIP/超级会员加速状态）."""
    try:
        with XunleiClient() as xl:
            task = xl.get_task(task_id)
    except ZSpaceError as exc:
        console.print(f"[bold red]查询迅雷任务详情失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(task, ensure_ascii=False))
        return

    params = task.get("params") or {}
    speedup_raw = params.get("speedup")
    speedup_obj: dict[str, Any] = {}
    if isinstance(speedup_raw, str) and speedup_raw.strip():
        try:
            speedup_obj = json.loads(speedup_raw)
        except Exception:
            speedup_obj = {}
    elif isinstance(speedup_raw, dict):
        speedup_obj = speedup_raw

    base_speed = int(params.get("speed") or 0)
    vip_speed = int(params.get("speedup_speed") or 0)
    speedup_status = str(params.get("speedup_status") or "0")
    speedup_count = int(params.get("speedup_count") or 0)
    speedup_failed = int(params.get("speedup_failed_count") or 0)
    svip_joined = str(params.get("super_speedup_isjoined") or "false") == "true"
    team_joined = str(params.get("team_isjoined") or "false") == "true"

    status_map = {
        "0": "未开启/空闲",
        "1": "加速准备中",
        "2": "已加速完成/加速生效",
        "3": "加速失败",
    }
    accel_summary = status_map.get(speedup_status, f"状态码 {speedup_status}")

    info_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    info_table.add_column("字段", style="bold cyan", no_wrap=True)
    info_table.add_column("值", style="white", overflow="fold")

    info_table.add_row("任务 ID", str(task.get("id") or ""))
    info_table.add_row("任务名称", str(task.get("name") or task.get("file_name") or ""))
    info_table.add_row("任务类型", str(task.get("type") or ""))
    info_table.add_row("状态阶段", _format_phase(str(task.get("phase") or ""), str(task.get("message") or "")))
    info_table.add_row("状态说明", str(task.get("message") or "-"))
    info_table.add_row("文件大小", f"{_format_bytes(task.get('file_size'))} ({task.get('file_size', 0)} B)")
    info_table.add_row("下载进度", f"{task.get('progress', 0)}%")
    info_table.add_row("基础下载速度", _format_speed(base_speed))
    info_table.add_row("会员加速速度", _format_speed(vip_speed))
    info_table.add_row("加速通道状态", f"{accel_summary} (成功子文件: {speedup_count}, 失败: {speedup_failed})")
    info_table.add_row("超级会员加速", "[bold green]已加入[/bold green]" if svip_joined else "未开启")
    info_table.add_row("组队加速", "[bold green]已加入[/bold green]" if team_joined else "未加入")
    if speedup_obj:
        info_table.add_row(
            "P2P/VIP/超会详情",
            f"P2P={speedup_obj.get('p2p', '-')}, VIP={speedup_obj.get('vip', '-')}, Super={speedup_obj.get('super', '-')}",
        )
    info_table.add_row("文件数量", str(params.get("total_file_count") or "1"))
    info_table.add_row("子文件索引", str(params.get("sub_file_index") or "-"))
    info_table.add_row("文件 ID", str(task.get("file_id") or params.get("file_id") or "-"))
    info_table.add_row("目录 ID", str(params.get("parent_folder_id") or "-"))
    info_table.add_row("简化保存路径", _extract_save_path(task))
    info_table.add_row("容器真实路径", str(params.get("real_path") or params.get("parent_folder_path") or "-"))
    if params.get("url"):
        info_table.add_row("原始下载链接", str(params.get("url")))
    if params.get("info_hash") or params.get("hash"):
        info_table.add_row("特征 Hash", str(params.get("info_hash") or params.get("hash")))
    info_table.add_row("创建时间", str(task.get("created_time") or "-"))
    info_table.add_row("更新时间", str(task.get("updated_time") or "-"))

    console.print(
        Panel(
            info_table,
            title=f"[bold cyan]迅雷任务详情: {task.get('name') or task_id}[/bold cyan]",
            border_style="cyan",
        )
    )


@xl_app.command("parse")
def xl_parse_cmd(
    url: str = typer.Argument(..., help="待解析的磁力链接 (magnet:?)、BT 特征码、HTTP/HTTPS 直链或电驴链接 (ed2k://)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出解析结果"),
) -> None:
    """解析磁力链接/直链/电驴链接 (`POST drive/v1/resource/list`)，展示包含的文件列表、大小与索引号."""
    try:
        with XunleiClient() as xl:
            parsed = xl.parse_url(url)
    except ZSpaceError as exc:
        console.print(f"[bold red]解析链接失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(parsed, ensure_ascii=False))
        return

    resources = parsed.get("resources") or []
    files = parsed.get("files") or []
    if not resources:
        console.print("[yellow]未能从该链接解析出任何资源。[/yellow]")
        return

    top = resources[0]
    top_name = str(top.get("name") or "未命名资源")
    top_size = _format_bytes(top.get("file_size"))
    top_count = int(top.get("file_count") or len(files) or 1)

    console.print(
        Panel(
            f"[bold]资源名称:[/bold] {top_name}\n"
            f"[bold]总计大小:[/bold] [magenta]{top_size}[/magenta] ({top.get('file_size', 0)} B)\n"
            f"[bold]文件总数:[/bold] {top_count} 个文件  |  [bold]解析批次 ID:[/bold] [dim]{parsed.get('list_id')}[/dim]",
            title="[bold green]迅雷资源解析成功[/bold green]",
            border_style="green",
        )
    )

    table = Table(
        title=f"包含文件列表 (共 {len(files)} 项，可通过 `zs xl add <url> --index <索引号>` 选择下载)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("索引号 (--index)", justify="right", style="bold yellow", no_wrap=True)
    table.add_column("文件名称", style="bold white", overflow="fold", min_width=24, ratio=3)
    table.add_column("大小", justify="right", style="magenta", no_wrap=True)
    table.add_column("MIME 类型", style="dim", overflow="fold", ratio=2)

    for f in files:
        table.add_row(
            str(f.get("file_index", 0)),
            str(f.get("name") or "-"),
            _format_bytes(f.get("file_size")),
            str(f.get("mime_type") or "-"),
        )
    console.print(table)


@xl_app.command("add")
def xl_add_cmd(
    url: str = typer.Argument(..., help="要添加的磁力链接、HTTP/HTTPS 直链、FTP 或电驴链接"),
    folder_id: Optional[str] = typer.Option(
        None,
        "--dir",
        "-d",
        help="目标下载目录 ID（不指定则自动使用默认『迅雷下载』目录，可通过 `zs xl dirs` 查看）",
    ),
    sub_file_index: Optional[str] = typer.Option(
        None,
        "--index",
        "-i",
        help="要下载的子文件索引号（逗号分隔如 `0,1,2`，不指定则默认下载全部文件）",
    ),
    name: Optional[str] = typer.Option(None, "--name", help="自定义任务名称（可选）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出创建结果"),
) -> None:
    """一键解析并添加磁力/HTTP/电驴链接到极空间迅雷下载 (`POST drive/v1/task`)."""
    try:
        with XunleiClient() as xl:
            res = xl.create_task(
                url=url,
                folder_id=folder_id,
                sub_file_index=sub_file_index,
                name=name,
            )
    except ZSpaceError as exc:
        console.print(f"[bold red]创建迅雷下载任务失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    task = (res.get("task") if isinstance(res, dict) else None) or res or {}
    tid = str(task.get("id") or "")
    tname = str(task.get("name") or task.get("file_name") or name or url)
    tsize = _format_bytes(task.get("file_size"))
    save_path = _extract_save_path(task)

    console.print(
        Panel(
            f"[bold]任务 ID:[/bold]   [cyan]{tid}[/cyan]\n"
            f"[bold]任务名称:[/bold] {tname}\n"
            f"[bold]文件大小:[/bold] [magenta]{tsize}[/magenta]\n"
            f"[bold]保存路径:[/bold] {save_path}\n"
            f"[bold]当前状态:[/bold] {_format_phase(str(task.get('phase') or PHASE_PENDING))}",
            title="[bold green]已成功添加迅雷下载任务[/bold green]",
            border_style="green",
        )
    )


@xl_app.command("pause")
def xl_pause_cmd(
    task_id: str = typer.Argument(..., help="要暂停的迅雷任务 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """暂停指定的迅雷下载任务 (`PATCH drive/v1/task`)."""
    try:
        with XunleiClient() as xl:
            res = xl.pause_task(task_id)
    except ZSpaceError as exc:
        console.print(f"[bold red]暂停迅雷任务失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    console.print(f"[bold yellow]已暂停迅雷任务:[/bold yellow] [cyan]{task_id}[/cyan]")


@xl_app.command("resume")
def xl_resume_cmd(
    task_id: str = typer.Argument(..., help="要恢复下载的迅雷任务 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """恢复指定的迅雷下载任务 (`PATCH drive/v1/task`)."""
    try:
        with XunleiClient() as xl:
            res = xl.resume_task(task_id)
    except ZSpaceError as exc:
        console.print(f"[bold red]恢复迅雷任务失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    console.print(f"[bold green]已恢复迅雷任务:[/bold green] [cyan]{task_id}[/cyan]")


@xl_app.command("rm")
def xl_rm_cmd(
    task_id: str = typer.Argument(..., help="要删除的迅雷任务 ID（多个 ID 可用逗号分隔）"),
    delete_file: bool = typer.Option(False, "--delete-file", "-f", help="同时删除已下载的本地文件"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """删除指定的迅雷任务 (`DELETE drive/v1/tasks`)，支持 `--delete-file` 同时删除本地文件."""
    try:
        with XunleiClient() as xl:
            res = xl.delete_task(task_id, delete_file=delete_file)
    except ZSpaceError as exc:
        console.print(f"[bold red]删除迅雷任务失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    deleted_ids = ", ".join(res.get("deleted") or [task_id])
    file_hint = "（已同步删除本地文件）" if delete_file else "（保留已下载本地文件）"
    console.print(f"[bold green]已删除迅雷任务:[/bold green] [cyan]{deleted_ids}[/cyan] {file_hint}")


@xl_app.command("dirs")
def xl_dirs_cmd(
    parent_id: str = typer.Option("", "--parent", "-p", help="浏览指定父目录 ID 下的子文件夹"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出目录信息"),
) -> None:
    """查看迅雷默认下载目录 (`device/download_paths`) 与存储挂载目录 (`drive/v1/files`)."""
    try:
        with XunleiClient() as xl:
            dl_paths = xl.get_download_paths()
            folders = xl.list_files(parent_id=parent_id)
    except ZSpaceError as exc:
        console.print(f"[bold red]获取迅雷目录列表失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(
            json.dumps(
                {"download_paths": dl_paths, "folders": folders},
                ensure_ascii=False,
            )
        )
        return

    if not parent_id and dl_paths:
        dl_table = Table(
            title="迅雷常用/默认下载目录 (device/download_paths)",
            box=box.ROUNDED,
            header_style="bold green",
        )
        dl_table.add_column("目录 ID (--dir)", style="cyan", no_wrap=True)
        dl_table.add_column("名称", style="bold white")
        dl_table.add_column("简化路径", style="yellow")
        dl_table.add_column("容器真实挂载路径", style="dim")

        for p in dl_paths:
            real_p = str(p.get("RealPath") or "")
            dl_table.add_row(
                str(p.get("Id") or ""),
                str(p.get("FileName") or "迅雷下载"),
                simplify_xunlei_path(real_p),
                real_p,
            )
        console.print(dl_table)

    f_table = Table(
        title=f"迅雷存储目录列表 (parent_id={parent_id or '根目录'})",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    f_table.add_column("文件夹 ID", style="cyan", no_wrap=True)
    f_table.add_column("名称", style="bold white")
    f_table.add_column("简化路径", style="yellow")
    f_table.add_column("已用 / 总空间", justify="right", style="magenta")
    f_table.add_column("可写", justify="center")
    f_table.add_column("说明", style="dim")

    for f in folders:
        f_params = f.get("params") or {}
        real_p = str(f_params.get("RealPath") or "")
        usage = f_params.get("usage")
        limit = f_params.get("limit")
        space_str = f"{_format_bytes(usage)} / {_format_bytes(limit)}" if limit else "-"
        writable = "[green]是[/green]" if str(f_params.get("is_write")) == "true" else "[red]否[/red]"
        cat_name = str(f_params.get("category_name") or "")
        if f_params.get("default") == "true":
            cat_name = f"{cat_name} (默认)" if cat_name else "默认"

        f_table.add_row(
            str(f.get("id") or ""),
            str(f.get("name") or ""),
            simplify_xunlei_path(real_p) or real_p or "-",
            space_str,
            writable,
            cat_name,
        )
    console.print(f_table)


@xl_app.command("status")
def xl_status_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出迅雷完整状态信息"),
) -> None:
    """查看迅雷客户端状态、版本、账号会员等级、默认下载目录与空间占用信息."""
    try:
        with XunleiClient() as xl:
            status_data = xl.get_status()
    except ZSpaceError as exc:
        console.print(f"[bold red]获取迅雷客户端状态失败:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(status_data, ensure_ascii=False))
        return

    dev_info = status_data.get("device_info") or {}
    runner = status_data.get("runner") or {}
    runner_params = runner.get("params") or {}
    user = dev_info.get("user") or {}
    vip = user.get("vip") or {}
    group_list = user.get("group") or []
    expires_at = group_list[0].get("expires_at", "-") if group_list and isinstance(group_list[0], dict) else "-"
    about_quota = (status_data.get("about") or {}).get("quota") or {}
    dl_paths = status_data.get("download_paths") or []
    default_dl_path = ""
    default_dl_id = ""
    if dl_paths and isinstance(dl_paths[0], dict):
        default_dl_path = str(dl_paths[0].get("RealPath") or "")
        default_dl_id = str(dl_paths[0].get("Id") or "")

    summary = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    summary.add_column("项目", style="bold cyan", no_wrap=True)
    summary.add_column("数值", style="white", overflow="fold")

    is_conn = bool(dev_info.get("is_connected", True))
    summary.add_row("连接状态", "[bold green]在线 (已连接)[/bold green]" if is_conn else "[bold red]离线[/bold red]")
    summary.add_row("设备名称", str(runner.get("name") or dev_info.get("product_name") or "极空间迅雷"))
    summary.add_row("Device Space", str(status_data.get("device_space") or ""))
    summary.add_row("目标 Space ID", str(status_data.get("space") or ""))
    summary.add_row(
        "迅雷版本",
        f"客户端 v{dev_info.get('client_version') or runner_params.get('client_version') or '-'}  |  "
        f"下载引擎 v{dev_info.get('download_version') or '-'}  |  "
        f"构建号 {dev_info.get('build_version') or '-'}",
    )
    is_vip = bool(vip.get("is_vip"))
    vip_desc = (
        f"[bold gold1]VIP 会员 (Lv.{vip.get('level', 0)}, 到期: {expires_at})[/bold gold1]"
        if is_vip
        else "普通用户"
    )
    summary.add_row("登录迅雷账号", f"{user.get('name') or '-'} (ID: {user.get('id') or '-'}) — {vip_desc}")
    if default_dl_path:
        summary.add_row(
            "默认下载目录",
            f"{simplify_xunlei_path(default_dl_path)}  [dim](ID: {default_dl_id}, 真实路径: {default_dl_path})[/dim]",
        )
    if about_quota:
        summary.add_row(
            "云盘空间配额",
            f"{_format_bytes(about_quota.get('usage'))} / {_format_bytes(about_quota.get('limit'))}",
        )

    console.print(
        Panel(
            summary,
            title="[bold cyan]极空间内嵌迅雷客户端状态 (zs xl status)[/bold cyan]",
            border_style="cyan",
        )
    )

    downloads = dev_info.get("downloads") or []
    user_mounts = [d for d in downloads if not d.get("is_root_path")] or downloads[:3]
    if user_mounts:
        m_table = Table(
            title="迅雷可见存储卷空间信息",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        m_table.add_column("卷名称", style="bold white")
        m_table.add_column("挂载路径", style="dim")
        m_table.add_column("已用空间", justify="right", style="yellow")
        m_table.add_column("总空间", justify="right", style="magenta")
        m_table.add_column("状态", justify="center")

        for m in user_mounts:
            err = str(m.get("errmsg") or "")
            m_table.add_row(
                str(m.get("name") or "默认数据卷"),
                str(m.get("path") or ""),
                _format_bytes(m.get("usage")),
                _format_bytes(m.get("limit")),
                f"[red]{err}[/red]" if err else "[green]正常[/green]",
            )
        console.print(m_table)
