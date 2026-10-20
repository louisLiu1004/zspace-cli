"""极空间原生下载中心 (`zs dl`) 命令模块.

逆向自 Webpack 模块 `14530` (`14422.6ddae533.js`, `15236.269cc226.js`, `26357.05b845fe.js`) 与 `63775`：
- `zs dl ls [--all | --completed | --running] [--share] [--json]`：查看极空间原生下载中心任务列表 (`/downloader/list`) 及队列详情 (`/downloader/tasks/details`)
- `zs dl add <url> [--path <save_path>] [--nas-torrent]`：添加下载链接 (`/downloader/add/link`)、NAS 内种子 (`/downloader/add/torrent/nas`) 或本地种子文件 (`/downloader/add/torrent`)
- `zs dl start [id] [--all]`：开始单个或全部任务 (`/downloader/start`, `/downloader/start/all`)
- `zs dl stop [id] [--all]`：暂停单个或全部任务 (`/downloader/stop`, `/downloader/stop/all`)
- `zs dl rm [id] [--delete-file] [--all]`：删除单个或全部任务 (`/downloader/del`, `/downloader/del/all`)
- `zs dl seed [--start-all | --stop-all]`：全部开始做种或停止做种 (`/downloader/start/seeding/all`, `/downloader/stop/seeding/all`)
- `zs dl config`：查看或设置下载中心配置 (`/downloader/get/setting`, `/downloader/set/setting`) 及服务开关 (`/manager/srvs`)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.core.client import ZSpaceClient, ZSpaceError

dl_app = typer.Typer(
    name="dl",
    help="极空间原生下载中心管理（Transmission/qBittorrent 双引擎任务、种子添加、做种与限速配置）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
# 导出别名供 main.py 自动识别挂载
app = dl_app
download_app = dl_app

console = Console(width=130 if not sys.stdout.isatty() else None)

BT_ENGINE_NAMES: dict[int, str] = {
    1: "Transmission",
    2: "qBittorrent",
}


def _format_bytes(num_bytes: int | float | str | None) -> str:
    """将字节数格式化为易读容量字符串."""
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


def _check_downloader_service(client: ZSpaceClient) -> dict[str, Any]:
    """查询 `/manager/srvs` 中 `TrAndAria`（极空间原生下载服务）的运行状态."""
    try:
        res = client.post("/manager/srvs")
        items = ((res.get("data") if isinstance(res, dict) else None) or {}).get("list") or []
        for item in items:
            if item.get("command") == "TrAndAria" or item.get("app_name") == "downloads":
                return item
    except Exception:
        pass
    return {}


def _handle_downloader_502(
    client: ZSpaceClient,
    exc: Exception,
    *,
    json_output: bool = False,
    extra_Share: bool = True,
) -> None:
    """当 `/downloader/*` 因后端 `TrAndAria` 服务未启动返回 HTTP 502 时，输出友好诊断信息."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 502:
        srv_info = _check_downloader_service(client)
        share_res: dict[str, Any] = {}
        if extra_Share:
            try:
                share_res = client.post(
                    "/downloader/share/list",
                    data={"start": 0, "size": 20, "num": 20, "type": "all"},
                )
            except Exception:
                share_res = {}

        if json_output:
            console.print_json(
                json.dumps(
                    {
                        "service_running": False,
                        "service": srv_info or {"command": "TrAndAria", "status": "stop"},
                        "message": "极空间原生下载服务 (TrAndAria) 当前未启动，网关返回 HTTP 502",
                        "hint": "可执行 `zs dl config --enable-service` 启动原生下载服务，或使用 `zs xl` 管理迅雷下载",
                        "share_downloads": share_res.get("data") if isinstance(share_res, dict) else None,
                    },
                    ensure_ascii=False,
                )
            )
            return

        status_str = str(srv_info.get("status") or "stop")
        desc_str = str(srv_info.get("desc") or "拥有Transmission/qBittorrent双引擎的下载工具")
        console.print(
            Panel(
                f"[bold yellow]极空间原生下载中心服务 (`TrAndAria`) 当前处于关闭状态 (status: {status_str})[/bold yellow]\n"
                f"[dim]{desc_str}[/dim]\n\n"
                f"• 开启原生下载服务: [bold cyan]zs dl config --enable-service[/bold cyan]\n"
                f"• 或使用已运行的迅雷服务: [bold green]zs xl ls[/bold green] / [bold green]zs xl status[/bold green]",
                title="[bold yellow]原生下载服务未运行 (HTTP 502)[/bold yellow]",
                border_style="yellow",
            )
        )
        return

    console.print(f"[bold red]请求极空间下载中心失败:[/bold red] {exc}")
    raise typer.Exit(code=1)


def _format_dl_status(task: dict[str, Any]) -> str:
    """格式化原生下载中心任务状态字段."""
    status = str(task.get("status") or task.get("state") or "")
    error = task.get("error")
    if error and str(error) not in ("0", ""):
        return f"[bold red]错误 ({task.get('errorString') or error})[/bold red]"
    mapping = {
        "0": "[yellow]已暂停[/yellow]",
        "1": "[yellow]等待校验[/yellow]",
        "2": "[cyan]校验中[/cyan]",
        "3": "[yellow]等待下载[/yellow]",
        "4": "[bold cyan]下载中[/bold cyan]",
        "5": "[yellow]等待做种[/yellow]",
        "6": "[bold green]做种中[/bold green]",
        "complete": "[bold green]已完成[/bold green]",
        "loading": "[bold cyan]下载中[/bold cyan]",
        "paused": "[yellow]已暂停[/yellow]",
        "stop": "[yellow]已停止[/yellow]",
        "seeding": "[bold green]做种中[/bold green]",
    }
    return mapping.get(status.lower(), status or "-")


@dl_app.command("ls")
def dl_list_cmd(
    all_tasks: bool = typer.Option(True, "--all", help="显示下载中与已完成全部任务（默认开启）"),
    completed: bool = typer.Option(False, "--completed", "-c", help="仅显示已完成任务 (type=complete)"),
    running: bool = typer.Option(False, "--running", "-r", help="仅显示进行中任务 (type=loading)"),
    share: bool = typer.Option(False, "--share", help="查看极空间闪电传/外链分享下载队列 (/downloader/share/list)"),
    limit: int = typer.Option(50, "--limit", "-n", help="单页最大返回条数"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出完整结果"),
) -> None:
    """查看极空间原生下载中心任务列表 (`/downloader/list`) 及队列详情 (`/downloader/tasks/details`)."""
    with ZSpaceClient(max_retries=0) as client:
        if share:
            try:
                res = client.post(
                    "/downloader/share/list",
                    data={"start": 0, "size": limit, "num": limit, "type": "all"},
                )
            except Exception as exc:
                console.print(f"[bold red]获取分享下载队列失败:[/bold red] {exc}")
                raise typer.Exit(code=1)

            if json_output:
                console.print_json(json.dumps(res, ensure_ascii=False))
                return

            data = res.get("data") or {}
            extra = res.get("extra") or {}
            items = data.get("list") or []
            table = Table(
                title=(
                    f"极空间分享下载队列 (共 {data.get('total', len(items))} 条 | "
                    f"进行中: {extra.get('pendingCount', 0)}, 完成: {extra.get('completeCount', 0)}, 失败: {extra.get('failCount', 0)})"
                ),
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("ID", style="cyan")
            table.add_column("名称", style="bold white")
            table.add_column("大小", justify="right", style="magenta")
            table.add_column("状态", justify="center")
            for item in items:
                table.add_row(
                    str(item.get("id") or ""),
                    str(item.get("name") or item.get("file_name") or "-"),
                    _format_bytes(item.get("size")),
                    _format_dl_status(item),
                )
            console.print(table)
            return

        query_types: list[str] = ["loading", "complete"]
        if completed and not running:
            query_types = ["complete"]
        elif running and not completed:
            query_types = ["loading"]
        elif all_tasks:
            query_types = ["loading", "complete"]

        tasks: list[dict[str, Any]] = []
        details_data: dict[str, Any] = {}
        try:
            for qtype in query_types:
                res = client.post(
                    "/downloader/list",
                    data={
                        "type": qtype,
                        "start": 0,
                        "size": limit,
                        "sortby": "addTime",
                        "order": "desc",
                    },
                )
                data = (res.get("data") if isinstance(res, dict) else None) or {}
                batch = data.get("list") or data.get("tasks") or []
                for item in batch:
                    if isinstance(item, dict):
                        item.setdefault("_queue_type", qtype)
                        tasks.append(item)

            det_res = client.post("/downloader/tasks/details")
            details_data = (det_res.get("data") if isinstance(det_res, dict) else None) or {}
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output)
            return

        if json_output:
            console.print_json(
                json.dumps(
                    {"tasks": tasks, "details": details_data},
                    ensure_ascii=False,
                )
            )
            return

        q_size = details_data.get("download_queue_size", "-")
        l_size = details_data.get("loading_queue_size", len([t for t in tasks if t.get("_queue_type") == "loading"]))
        table = Table(
            title=f"极空间原生下载中心任务列表 (共 {len(tasks)} 条 | 活跃队列: {l_size}/{q_size})",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("任务名称", style="bold white", overflow="fold", max_width=40)
        table.add_column("总大小", justify="right", style="magenta", no_wrap=True)
        table.add_column("进度", justify="right", no_wrap=True)
        table.add_column("下载速度", justify="right", style="green", no_wrap=True)
        table.add_column("上传速度", justify="right", style="blue", no_wrap=True)
        table.add_column("状态", justify="center", no_wrap=True)
        table.add_column("保存目录", style="dim", overflow="fold", max_width=36)

        for t in tasks:
            tid = str(t.get("id") or "")
            name = str(t.get("name") or "-")
            size_str = _format_bytes(t.get("totalSize") or t.get("size"))
            pct = float(t.get("percentDone") or t.get("progress") or 0)
            if 0 < pct <= 1.0 and "percentDone" in t:
                pct *= 100.0
            prog_str = f"[green]{pct:.1f}%[/green]" if pct >= 100 else f"[yellow]{pct:.1f}%[/yellow]"
            dl_speed = _format_speed(t.get("rateDownload") or t.get("downSpeed"))
            ul_speed = _format_speed(t.get("rateUpload") or t.get("upSpeed"))
            status_str = _format_dl_status(t)
            save_dir = str(t.get("downloadDir") or t.get("dir") or "-")
            table.add_row(tid, name, size_str, prog_str, dl_speed, ul_speed, status_str, save_dir)

        console.print(table)


@dl_app.command("add")
def dl_add_cmd(
    url: str = typer.Argument(..., help="下载链接 (magnet/HTTP/FTP/ed2k)、NAS 内 .torrent 路径或本机 .torrent 文件路径"),
    save_path: Optional[str] = typer.Option(
        None,
        "--path",
        "-p",
        help="NAS 上的保存目录路径（不指定则自动查询 `/downloader/get/setting` 的默认 `download_dir`）",
    ),
    nas_torrent: bool = typer.Option(
        False,
        "--nas-torrent",
        help="显式将参数作为 NAS 内部存储的 `.torrent` 种子文件路径添加 (`/downloader/add/torrent/nas`)",
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """添加下载链接 (`/downloader/add/link`)、NAS 内种子 (`/downloader/add/torrent/nas`) 或本地种子 (`/downloader/add/torrent`)."""
    with ZSpaceClient(max_retries=0) as client:
        try:
            target_dir = save_path or ""
            if not target_dir:
                setting_res = client.post("/downloader/get/setting")
                sdata = (setting_res.get("data") if isinstance(setting_res, dict) else None) or {}
                target_dir = str(sdata.get("download_dir") or "")

            clean_target = url.strip()
            local_path = Path(clean_target).expanduser()

            if not nas_torrent and local_path.is_file() and local_path.suffix.lower() == ".torrent":
                # 上传本地 .torrent 文件到 /downloader/add/torrent
                with local_path.open("rb") as fh:
                    files = {"file": (local_path.name, fh, "application/x-bittorrent")}
                    res = client.post_multipart(
                        "/downloader/add/torrent",
                        files=files,
                        data={"dir": target_dir},
                    )
                mode_desc = "本地 BT 种子上传 (/downloader/add/torrent)"
            elif nas_torrent or (clean_target.startswith("/") and clean_target.lower().endswith(".torrent")):
                # 添加 NAS 内部种子路径 /downloader/add/torrent/nas
                res = client.post(
                    "/downloader/add/torrent/nas",
                    data={"path": clean_target, "dir": target_dir},
                )
                mode_desc = "NAS 内 BT 种子添加 (/downloader/add/torrent/nas)"
            else:
                # 添加磁力/HTTP/FTP/电驴链接 /downloader/add/link
                res = client.post(
                    "/downloader/add/link",
                    data={"uri": clean_target, "dir": target_dir},
                )
                mode_desc = "链接下载添加 (/downloader/add/link)"
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            raise typer.Exit(code=1)

        if json_output:
            console.print_json(json.dumps(res, ensure_ascii=False))
            return

        console.print(
            Panel(
                f"[bold]添加方式:[/bold] {mode_desc}\n"
                f"[bold]下载目标:[/bold] {clean_target}\n"
                f"[bold]保存目录:[/bold] [yellow]{target_dir or '(默认目录)'}[/yellow]",
                title="[bold green]已成功提交到极空间下载中心[/bold green]",
                border_style="green",
            )
        )


@dl_app.command("start")
def dl_start_cmd(
    task_id: Optional[str] = typer.Argument(None, help="要开始的任务 ID（若使用 `--all` 可省略）"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="开始全部任务 (`/downloader/start/all`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """开始/恢复单个下载任务 (`/downloader/start`) 或全部任务 (`/downloader/start/all`)."""
    if not all_tasks and not task_id:
        console.print("[bold red]请指定任务 ID 或使用 `--all` 开始全部任务。[/bold red]")
        raise typer.Exit(code=1)

    with ZSpaceClient(max_retries=0) as client:
        try:
            if all_tasks:
                res = client.post("/downloader/start/all")
            else:
                res = client.post("/downloader/start", data={"id": task_id})
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    target_desc = "全部下载任务" if all_tasks else f"任务 [cyan]{task_id}[/cyan]"
    console.print(f"[bold green]已开始 {target_desc}[/bold green]")


@dl_app.command("stop")
def dl_stop_cmd(
    task_id: Optional[str] = typer.Argument(None, help="要暂停的任务 ID（若使用 `--all` 可省略）"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="暂停全部任务 (`/downloader/stop/all`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """暂停单个下载任务 (`/downloader/stop`) 或全部任务 (`/downloader/stop/all`)."""
    if not all_tasks and not task_id:
        console.print("[bold red]请指定任务 ID 或使用 `--all` 暂停全部任务。[/bold red]")
        raise typer.Exit(code=1)

    with ZSpaceClient(max_retries=0) as client:
        try:
            if all_tasks:
                res = client.post("/downloader/stop/all")
            else:
                res = client.post("/downloader/stop", data={"id": task_id})
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    target_desc = "全部下载任务" if all_tasks else f"任务 [cyan]{task_id}[/cyan]"
    console.print(f"[bold yellow]已暂停 {target_desc}[/bold yellow]")


@dl_app.command("rm")
def dl_rm_cmd(
    task_id: Optional[str] = typer.Argument(None, help="要删除的任务 ID（若使用 `--all` 可省略）"),
    delete_file: bool = typer.Option(False, "--delete-file", "-f", help="同时删除已下载的本地文件 (`remove=1`)"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="清空指定类别的全部任务 (`/downloader/del/all`)"),
    del_type: str = typer.Option(
        "complete",
        "--type",
        "-t",
        help="配合 `--all` 使用的批量删除类型: `complete`(已完成) | `loading`(下载中) | `fail`(失败) | `seeding-stopped`(暂停做种)",
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """删除单个下载任务 (`/downloader/del`) 或批量删除全部任务 (`/downloader/del/all`)."""
    if not all_tasks and not task_id:
        console.print("[bold red]请指定任务 ID 或使用 `--all` 批量删除任务。[/bold red]")
        raise typer.Exit(code=1)

    remove_flag = 1 if delete_file else 0
    with ZSpaceClient(max_retries=0) as client:
        try:
            if all_tasks:
                res = client.post(
                    "/downloader/del/all",
                    data={"type": del_type, "remove": remove_flag},
                )
            else:
                res = client.post(
                    "/downloader/del",
                    data={"id": task_id, "remove": remove_flag},
                )
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    file_hint = "（已同步删除本地文件）" if delete_file else "（保留本地文件）"
    target_desc = f"全部 `{del_type}` 类别任务" if all_tasks else f"任务 [cyan]{task_id}[/cyan]"
    console.print(f"[bold green]已删除 {target_desc}[/bold green] {file_hint}")


@dl_app.command("seed")
def dl_seed_cmd(
    start_all: bool = typer.Option(False, "--start-all", help="开始全部做种任务 (`/downloader/start/seeding/all`)"),
    stop_all: bool = typer.Option(False, "--stop-all", help="停止全部做种任务 (`/downloader/stop/seeding/all`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出响应"),
) -> None:
    """BT/PT 做种控制：全部开始做种 (`/downloader/start/seeding/all`) 或全部停止做种 (`/downloader/stop/seeding/all`)."""
    if start_all == stop_all:
        console.print("[bold red]请指定 `--start-all`（全部开始做种）或 `--stop-all`（全部停止做种）之一。[/bold red]")
        raise typer.Exit(code=1)

    endpoint = "/downloader/start/seeding/all" if start_all else "/downloader/stop/seeding/all"
    with ZSpaceClient(max_retries=0) as client:
        try:
            res = client.post(endpoint)
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            raise typer.Exit(code=1)

    if json_output:
        console.print_json(json.dumps(res, ensure_ascii=False))
        return

    if start_all:
        console.print("[bold green]已开启全部 BT/PT 任务做种 (/downloader/start/seeding/all)[/bold green]")
    else:
        console.print("[bold yellow]已停止全部 BT/PT 任务做种 (/downloader/stop/seeding/all)[/bold yellow]")


@dl_app.command("config")
def dl_config_cmd(
    queue_size: Optional[int] = typer.Option(None, "--queue-size", help="设置最大同时下载任务数 (`download_queue_size`, 如 5/10/20/50/100)"),
    download_dir: Optional[str] = typer.Option(None, "--download-dir", help="设置默认下载保存目录 (`download_dir`)"),
    watch_dir: Optional[str] = typer.Option(None, "--watch-dir", help="设置 BT 种子监控文件夹路径 (`torrent_watch_dir`)"),
    clear_watch_dir: bool = typer.Option(False, "--clear-watch-dir", help="清除 BT 种子监控文件夹 (`torrent_watch_dir_clear=true`)"),
    up_limit: Optional[int] = typer.Option(None, "--up-limit", help="设置最大上传限速 KB/s (`speed_limit_up`, 0~999999)"),
    up_limit_enable: Optional[bool] = typer.Option(None, "--up-limit-enable/--no-up-limit-enable", help="开启或关闭上传限速 (`speed_limit_up_enable`)"),
    down_limit: Optional[int] = typer.Option(None, "--down-limit", help="设置最大下载限速 KB/s (`speed_limit_down`, 0~999999)"),
    down_limit_enable: Optional[bool] = typer.Option(None, "--down-limit-enable/--no-down-limit-enable", help="开启或关闭下载限速 (`speed_limit_down_enable`)"),
    peer_port: Optional[int] = typer.Option(None, "--peer-port", help="设置 BT 监听端口 (`peer_port`, 10000~65535)"),
    enable_service: bool = typer.Option(False, "--enable-service", help="启动极空间原生下载后台服务 (`/manager/srv/start`, `command=TrAndAria`)"),
    disable_service: bool = typer.Option(False, "--disable-service", help="停止极空间原生下载后台服务 (`/manager/srv/stop`, `command=TrAndAria`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出配置详情"),
) -> None:
    """查看或修改极空间原生下载中心配置 (`/downloader/get/setting`, `/downloader/set/setting`)."""
    with ZSpaceClient(max_retries=0) as client:
        # 1. 处理后台服务启停开关
        if enable_service or disable_service:
            srv_ep = "/manager/srv/start" if enable_service else "/manager/srv/stop"
            srv_res = client.post(srv_ep, data={"command": "TrAndAria"})
            if json_output and not any(
                x is not None
                for x in (queue_size, download_dir, watch_dir, up_limit, up_limit_enable, down_limit, down_limit_enable, peer_port)
            ) and not clear_watch_dir:
                console.print_json(json.dumps(srv_res, ensure_ascii=False))
                return
            action_cn = "已启动" if enable_service else "已停止"
            console.print(f"[bold green]{action_cn}极空间原生下载服务 (TrAndAria)[/bold green]")
            if disable_service:
                return

        # 2. 组装需要修改的设置项
        updates: dict[str, Any] = {}
        if queue_size is not None:
            updates["download_queue_size"] = queue_size
        if download_dir is not None:
            updates["download_dir"] = download_dir
        if watch_dir is not None:
            updates["torrent_watch_dir"] = watch_dir
        if clear_watch_dir:
            updates["torrent_watch_dir_clear"] = True
        if up_limit is not None:
            updates["speed_limit_up"] = up_limit
        if up_limit_enable is not None:
            updates["speed_limit_up_enable"] = up_limit_enable
        if down_limit is not None:
            updates["speed_limit_down"] = down_limit
        if down_limit_enable is not None:
            updates["speed_limit_down_enable"] = down_limit_enable
        if peer_port is not None:
            updates["peer_port"] = peer_port

        try:
            if updates:
                client.post("/downloader/set/setting", data=updates)
            res = client.post("/downloader/get/setting")
        except Exception as exc:
            _handle_downloader_502(client, exc, json_output=json_output, extra_Share=False)
            return

        srv_info = _check_downloader_service(client)

    if json_output:
        out_payload = {
            "service": srv_info,
            "setting": (res.get("data") if isinstance(res, dict) else None) or res,
            "updated": updates if updates else None,
        }
        console.print_json(json.dumps(out_payload, ensure_ascii=False))
        return

    sdata = (res.get("data") if isinstance(res, dict) else None) or {}
    engine_type = int(sdata.get("bt_engine_type") or 1)
    engine_map = dict(BT_ENGINE_NAMES)
    for eng in sdata.get("bt_engine_type_list") or []:
        if isinstance(eng, dict) and eng.get("type"):
            engine_map[int(eng["type"])] = f"{eng.get('name')} ({eng.get('version')})"

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("配置项", style="bold cyan", no_wrap=True)
    table.add_column("当前值", style="white", overflow="fold")

    srv_status = str(srv_info.get("status") or "running")
    table.add_row(
        "服务状态 (TrAndAria)",
        "[bold green]运行中 (running)[/bold green]" if srv_status == "running" else f"[yellow]{srv_status}[/yellow]",
    )
    table.add_row("当前 BT/PT 引擎", f"[bold magenta]{engine_map.get(engine_type, str(engine_type))}[/bold magenta]")
    table.add_row("默认下载目录 (download_dir)", str(sdata.get("download_dir") or "-"))
    table.add_row("种子监控目录 (torrent_watch_dir)", str(sdata.get("torrent_watch_dir") or "(未设置)"))
    table.add_row("最大同时下载数 (download_queue_size)", str(sdata.get("download_queue_size") or "-"))
    table.add_row("BT 监听端口 (peer_port)", str(sdata.get("peer_port") or "-"))
    table.add_row("单任务最大连接数 (peer_limit_torrent_max)", str(sdata.get("peer_limit_torrent_max") or "-"))
    down_en = bool(sdata.get("speed_limit_down_enable"))
    up_en = bool(sdata.get("speed_limit_up_enable"))
    table.add_row(
        "下载限速",
        f"[yellow]开启 ({sdata.get('speed_limit_down', 0)} KB/s)[/yellow]" if down_en else "[green]不限速[/green]",
    )
    table.add_row(
        "上传限速",
        f"[yellow]开启 ({sdata.get('speed_limit_up', 0)} KB/s)[/yellow]" if up_en else "[green]不限速[/green]",
    )
    ratio_en = bool(sdata.get("seed_ratio_limit_enable"))
    time_en = bool(sdata.get("seed_time_limit_enable"))
    table.add_row(
        "分享率限制",
        f"开启 ({sdata.get('seed_ratio_limit')})" if ratio_en else "关闭",
    )
    table.add_row(
        "做种时间限制",
        f"开启 ({sdata.get('seed_time_limit')} 分钟)" if time_en else "关闭",
    )

    console.print(
        Panel(
            table,
            title="[bold cyan]极空间原生下载中心配置 (zs dl config)[/bold cyan]",
            border_style="cyan",
        )
    )
