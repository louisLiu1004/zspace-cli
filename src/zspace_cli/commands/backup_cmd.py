"""极空间 `zs backup` 备份中心与 `zs nvr` 监控中心（只读）.

真机探测验证结果：
1. 备份中心 (Backup)：
   - `POST /v2/crossdevice/backup/list`: 跨设备/本地备份任务列表（code=200）
   - `POST /znetdisk/autobackup/info`: 网盘自动备份状态与统计（code=200）
   - `POST /znetdisk/autobackup/faillist`: 网盘自动备份失败明细列表（code=200）
   - `POST /znetdisk/sync/home`、`POST /znetdisk/sync/list`: 网盘同步状态与目录列表（code=200）
   - `POST /v2/album/db/backupInfo`: 相册数据库自动备份配置（code=200）
   - `GET /docsync/info`: 文档同步 Syncthing 数据库配置（code=200）
   - 接口 `/zbakcenter/resource/*` 与 `/zbakv2/task/*` 在真机返回 502，原因：后台守护进程
     `netdiskv2_serve` / `backup_nas` 处于未运行状态 (status=stop)。
2. 监控中心 (NVR):
   - 套件未安装（AppStore `app_name="camera"`, `installed=0`，后台无运行服务）。
   - 接口 `/AppStore/znvr/*` 在网关直接返回 HTTP 404 Not Found。
   - 依只读及真机验证规则，`nvr_app` 仅提供状态提示，不封装未安装的业务命令。
"""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands._common import (
    call,
    call_data,
    console,
    human_bytes,
    human_time,
    print_json,
    yes_no,
)

# ==============================================================================
# 备份中心 (zs backup)
# ==============================================================================

backup_app = typer.Typer(
    name="backup",
    help="备份中心：跨设备备份、网盘自动备份、双向同步与数据库备份（只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = backup_app


@backup_app.command("status")
def backup_status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """备份中心总体运行状态：服务进程、网盘自动备份、同步与数据库备份概览."""
    # 获取网盘自动备份状态
    auto_resp = call("POST", "/znetdisk/autobackup/info")
    auto_data = (auto_resp.get("data") or {}) if isinstance(auto_resp, dict) else {}

    # 获取跨设备备份任务
    tasks_resp = call("POST", "/v2/crossdevice/backup/list", data={"start": 0, "num": 10})
    tasks_data = (tasks_resp.get("data") or {}) if isinstance(tasks_resp, dict) else {}

    # 获取网盘同步状态
    sync_resp = call("POST", "/znetdisk/sync/home")
    sync_data = (sync_resp.get("data") or {}) if isinstance(sync_resp, dict) else {}

    # 获取相册数据库备份信息
    album_resp = call("POST", "/v2/album/db/backupInfo")
    album_data = (album_resp.get("data") or {}) if isinstance(album_resp, dict) else {}

    # 获取服务列表
    srvs_resp = call("POST", "/manager/srvs")
    srv_list = (srvs_resp.get("data") or {}).get("list") or []
    backup_srvs = [
        s for s in srv_list
        if s.get("app_name") in ("backupCenter", "autoBackup", "cloudBackUp")
        or s.get("command") in ("file_sync", "backup_nas", "syncthing", "netdiskv2_serve", "zdrive", "zonedrive")
    ]

    if as_json:
        print_json({
            "auto_backup": auto_data,
            "crossdevice_tasks": tasks_data,
            "sync": sync_data,
            "album_db_backup": album_data,
            "services": backup_srvs,
        })
        return

    # 1. 概览面板
    is_auto_open = str(auto_data.get("is_open")) in ("1", "true")
    uploaded_bytes = auto_data.get("uploaded_size") or 0
    upload_num = auto_data.get("upload_num") or 0
    fail_num = auto_data.get("fail_num") or 0
    next_time = human_time(auto_data.get("next_backup_time"))

    is_sync_open = str(sync_data.get("is_open")) in ("1", "true")
    task_total = tasks_data.get("total") or len(tasks_data.get("list") or [])

    db_pool = album_data.get("backupPoolName") or "-"
    db_freq = f"每 {album_data.get('frequencyDays')} 天 (保留 {album_data.get('retainNum')} 份)" if album_data.get("frequencyDays") else "-"

    panel_text = (
        f"网盘自动备份      {yes_no(is_auto_open)}  已备份: [bold]{human_bytes(uploaded_bytes)}[/bold] ({upload_num:,} 个文件)  异常: {fail_num} 个\n"
        f"下次备份时间      {next_time}\n"
        f"网盘同步状态      {yes_no(is_sync_open)}\n"
        f"跨设备本地任务    共 {task_total} 个任务\n"
        f"相册数据库备份    存储池 [bold]{db_pool}[/bold] | 策略: {db_freq}"
    )
    console.print(Panel(panel_text, title="极空间备份中心概览", border_style="cyan"))

    # 2. 相关后台服务状态表
    if backup_srvs:
        t = Table(title="备份相关系统服务", box=box.SIMPLE_HEAVY)
        for col in ("服务名称", "命令标识", "所属模块", "运行状态", "描述"):
            t.add_column(col, overflow="fold")
        for s in backup_srvs:
            st = s.get("status")
            st_text = "[green]运行中[/green]" if st == "running" else f"[dim]{st}[/dim]"
            t.add_row(
                str(s.get("name") or "-"),
                str(s.get("command") or "-"),
                str(s.get("app_name") or "-"),
                st_text,
                str(s.get("desc") or "-"),
            )
        console.print(t)


@backup_app.command("tasks")
def list_tasks(
    start: int = typer.Option(0, "--start", "-s", help="分页起始偏移量"),
    limit: int = typer.Option(20, "--limit", "-n", help="返回任务数量"),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    """跨设备与本地备份任务列表 (/v2/crossdevice/backup/list)."""
    resp = call("POST", "/v2/crossdevice/backup/list", data={"start": start, "num": limit})
    d = (resp.get("data") or {}) if isinstance(resp, dict) else {}
    tasks = d.get("list") or []
    total = d.get("total", len(tasks))

    if as_json:
        print_json(d)
        return

    if not tasks:
        console.print(f"当前暂无跨设备/本地备份任务 (总数: {total})。可通过客户端「备份中心」新建任务。")
        return

    t = Table(title=f"跨设备/本地备份任务 (共 {total} 个)", box=box.SIMPLE_HEAVY)
    for col in ("ID", "任务名称", "源目录", "目标存储", "备份周期(小时)", "休眠备份", "状态"):
        t.add_column(col, overflow="fold")
    for item in tasks:
        sources = "、".join(src.get("path", "") for src in (item.get("source_info") or []))
        t.add_row(
            str(item.get("id")),
            str(item.get("name")),
            sources or "-",
            str(item.get("save_path") or "-"),
            str(item.get("period") or "-"),
            "是" if item.get("ignore_disk_status") == "yes" else "否",
            str(item.get("status") or "-"),
        )
    console.print(t)


@backup_app.command("auto")
def auto_backup(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """网盘自动备份状态与当前上传进度 (/znetdisk/autobackup/info)."""
    resp = call("POST", "/znetdisk/autobackup/info")
    d = (resp.get("data") or {}) if isinstance(resp, dict) else {}

    if as_json:
        print_json(d)
        return

    is_open = str(d.get("is_open")) in ("1", "true")
    paths = "、".join(d.get("paths") or [])
    up_size = human_bytes(d.get("uploaded_size"))
    up_num = d.get("upload_num", 0)
    fail_num = d.get("fail_num", 0)
    next_time = human_time(d.get("next_backup_time"))

    body = (
        f"功能开关        {yes_no(is_open)}\n"
        f"备份源路径      {paths or '-'}\n"
        f"目标网盘路径    {d.get('save_path') or '-'}\n"
        f"已备份数据量    [bold]{up_size}[/bold] ({up_num:,} 个文件)\n"
        f"备份失败文件    [red]{fail_num}[/red] 个 (使用 `zs backup fails` 查看明细)\n"
        f"下次备份时间    {next_time}\n"
        f"每日执行周期    {d.get('start_time') or '-'} (周期间隔: {d.get('exec_cycle')} 天)"
    )
    console.print(Panel(body, title="网盘自动备份状态 (百度网盘)", border_style="cyan"))

    # 当前上传/排队队列
    upload_list = d.get("upload_list") or []
    if upload_list:
        t = Table(title=f"当前备份/排队队列 ({len(upload_list)} 个)", box=box.SIMPLE_HEAVY)
        for col in ("文件名称", "已上传", "总大小", "速度", "失败原因"):
            t.add_column(col, overflow="fold")
        for f in upload_list:
            t.add_row(
                str(f.get("file_name")),
                human_bytes(f.get("uploaded_size")),
                human_bytes(f.get("total_size")),
                f"{human_bytes(f.get('speed'))}/s" if f.get("speed") else "-",
                str(f.get("fail_reason") or "-"),
            )
        console.print(t)


@backup_app.command("fails")
def backup_fails(
    page: int = typer.Option(1, "--page", "-p", help="分页页码"),
    limit: int = typer.Option(20, "--limit", "-n", help="每页条数"),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    """网盘自动备份失败文件列表 (/znetdisk/autobackup/faillist)."""
    resp = call("POST", "/znetdisk/autobackup/faillist", data={"page": page, "limit": limit})
    d = (resp.get("data") or {}) if isinstance(resp, dict) else {}
    items = d.get("list") or []
    total = d.get("total", len(items))

    if as_json:
        print_json(d)
        return

    if not items:
        console.print(f"[green]网盘自动备份无失败记录[/green] (总记录数: {total})")
        return

    t = Table(title=f"备份失败记录 (第 {page} 页 / 共 {total} 条)", box=box.SIMPLE_HEAVY)
    for col in ("文件名", "文件大小", "失败原因", "重试次数", "更新时间", "完整路径"):
        t.add_column(col, overflow="fold")
    for it in items:
        t.add_row(
            str(it.get("file_name") or "-"),
            human_bytes(it.get("file_size")),
            str(it.get("fail_reason") or "-"),
            str(it.get("retry_times", 0)),
            human_time(it.get("update_at")),
            str(it.get("file_path") or "-"),
        )
    console.print(t)


@backup_app.command("sync")
def sync_status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """网盘双向/单向同步目录与运行状态 (/znetdisk/sync/home & list)."""
    home_resp = call("POST", "/znetdisk/sync/home")
    home_data = (home_resp.get("data") or {}) if isinstance(home_resp, dict) else {}

    list_resp = call("POST", "/znetdisk/sync/list")
    list_data = (list_resp.get("data") or {}) if isinstance(list_resp, dict) else {}
    sync_dirs = list_data.get("list") or []

    if as_json:
        print_json({"status": home_data, "dirs": sync_dirs})
        return

    is_open = str(home_data.get("is_open")) in ("1", "true")
    console.print(
        f"同步功能开关：{yes_no(is_open)}  待下载任务：{home_data.get('download_wait', 0)}  "
        f"本地保存目录：{home_data.get('save_path') or '-'}"
    )

    if sync_dirs:
        t = Table(title=f"同步目录列表 (共 {len(sync_dirs)} 个)", box=box.SIMPLE_HEAVY)
        for col in ("目录ID", "网盘路径"):
            t.add_column(col, overflow="fold")
        for sd in sync_dirs:
            t.add_row(str(sd.get("id")), str(sd.get("path")))
        console.print(t)
    else:
        console.print("[dim]当前未配置同步目录。[/dim]")


@backup_app.command("db")
def db_backup(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """相册数据库与系统文档数据库备份配置 (/v2/album/db/backupInfo & /docsync/info)."""
    album_resp = call("POST", "/v2/album/db/backupInfo")
    album_data = (album_resp.get("data") or {}) if isinstance(album_resp, dict) else {}

    docsync_resp = call("GET", "/docsync/info")
    docsync_data = (docsync_resp.get("data") or {}) if isinstance(docsync_resp, dict) else {}

    if as_json:
        print_json({"album_db": album_data, "docsync": docsync_data})
        return

    # 相册数据库
    last_bak = human_time(album_data.get("lastBackupTime"))
    supp_freq = "、".join(f"{d}天" for d in (album_data.get("supportedFrequencyDays") or []))
    supp_nums = "、".join(f"{n}份" for n in (album_data.get("supportedRetainNums") or []))
    body = (
        f"备份存储池      [bold]{album_data.get('backupPoolName') or '-'}[/bold]\n"
        f"备份存储路径    {album_data.get('backupPath') or '-'}\n"
        f"备份周期        每 {album_data.get('frequencyDays')} 天\n"
        f"保留份数        保留 {album_data.get('retainNum')} 份\n"
        f"上次备份时间    {last_bak}\n"
        f"支持周期选项    {supp_freq or '-'}\n"
        f"支持保留选项    {supp_nums or '-'}"
    )
    console.print(Panel(body, title="极相册数据库自动备份配置", border_style="cyan"))

    # 文档同步数据库
    st_set = docsync_data.get("syncthing_setting") or {}
    console.print(
        f"文档同步 (Syncthing) 数据库路径：[bold]{st_set.get('database_path') or '-'}[/bold]  "
        f"大小：{human_bytes(docsync_data.get('database_size'))}  "
        f"可选路径：{'、'.join(docsync_data.get('database_path_list') or [])}"
    )


# ==============================================================================
# 监控中心 (zs nvr)
# ==============================================================================

nvr_app = typer.Typer(
    name="nvr",
    help="监控中心 (NVR)：摄像头、监控回放与存储管理（本机未安装此套件）",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@nvr_app.callback(invoke_without_command=True)
def nvr_callback(ctx: typer.Context) -> None:
    """监控中心 (NVR) 主命令入口."""
    if ctx.invoked_subcommand is None:
        console.print(
            Panel(
                "[yellow]当前极空间 NAS 设备未安装「监控中心 (NVR)」套件 (app_name: camera)[/yellow]\n\n"
                "• 应用商店状态：[dim]installed=0 (未安装)[/dim]\n"
                "• 后台服务状态：[dim]无 NVR 守护进程运行[/dim]\n"
                "• 核心接口探测：[dim]/AppStore/znvr/* 均返回 HTTP 404 (应用未安装/路由不存在)[/dim]\n"
                "• 如需使用，请先在极空间客户端或应用中心安装「监控中心」套件。",
                title="监控中心 (NVR) 状态提示",
                border_style="yellow",
            )
        )
