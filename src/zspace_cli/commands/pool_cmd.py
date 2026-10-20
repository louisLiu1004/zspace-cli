"""极空间 `zs pool` 存储池、物理硬盘 SMART、快照、SSD 缓存与外置 USB/eSATA 管理命令组.

涵盖 API 模块：
- 模块 83787：ZSPool 存储池与磁盘管理 (`/zspool/info`, `/zspool/hardware/info`, `/zspool/smart/report2`, `/zspool/cache/hits`)
- 模块 52324：磁盘空间分类占用 (`/system/diskusage3`, `/system/diskusage/status`)
- 模块 89794：存储池快照管理 (`/zspool/snapshot/*`)
- 模块 67420 & 41081：虚拟 USB (`/zspool/vusb/*`) 与 eSATA 外置存储 (`/zspool/esata/*`)
"""

from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands.auth_cmd import _aggregate_disk_usage, format_bytes
from zspace_cli.core.client import ZSpaceClient, ZSpaceError
from zspace_cli.core.config import mask_secret

pool_app = typer.Typer(
    name="pool",
    help="存储池 (ZSPool)、物理硬盘 SMART 健康报告、Btrfs/ZFS 快照、SSD 缓存与外接存储管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
snapshot_app = typer.Typer(
    name="snapshot",
    help="存储池快照 (Snapshot) 查看、创建与删除管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
pool_app.add_typer(snapshot_app, name="snapshot")

# 导出别名供 main.py 自动挂载
app = pool_app
console = Console()


def _as_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_pool_payload(client: ZSpaceClient) -> dict[str, Any]:
    """调用 `/zspool/info` 并返回 `data` 字典."""
    resp = client.pool_info()
    return resp.get("data") or {}


# ── 1. 存储池列表与详情 (`zs pool ls` / `zs pool info`) ──


@pool_app.command("ls")
def ls_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出所有存储池容量与健康状态概览 (`/zspool/info`)."""
    with ZSpaceClient() as client:
        data = _fetch_pool_payload(client)
        pool_list = data.get("pool_list") or []

        if json_output:
            console.print(json.dumps(pool_list, ensure_ascii=False, indent=2))
            return

        if not pool_list:
            console.print("[yellow]当前未发现已挂载的存储池。[/yellow]")
            return

        table = Table(
            title=f"极空间存储池列表 (协议版本: {data.get('esp_ver', 'v3')})",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("池 ID", justify="right", style="yellow")
        table.add_column("名称", style="bold")
        table.add_column("RAID 模式", style="magenta")
        table.add_column("协议", style="dim")
        table.add_column("硬盘数", justify="right")
        table.add_column("总容量", justify="right")
        table.add_column("已用空间", justify="right")
        table.add_column("可用空间", justify="right")
        table.add_column("使用率", justify="right")
        table.add_column("状态")

        for p in pool_list:
            total_b = _as_int(p.get("total_size"))
            free_b = _as_int(p.get("free_size"))
            used_b = _as_int(p.get("usage_size")) or max(0, total_b - free_b)
            pct = (used_b / total_b * 100.0) if total_b else 0.0
            pct_color = "red" if pct >= 85 else ("yellow" if pct >= 70 else "green")
            st = str(p.get("status") or "ok")
            st_badge = "[green]正常 (ok)[/green]" if st.lower() == "ok" else f"[red]{st}[/red]"
            disks = p.get("disk_list") or []

            table.add_row(
                str(p.get("id", "")),
                str(p.get("name", "")),
                str(p.get("pool_type") or "-").upper(),
                str(p.get("protocol") or "-"),
                f"{len(disks)} 盘",
                format_bytes(total_b),
                format_bytes(used_b),
                format_bytes(free_b),
                f"[{pct_color}]{pct:.1f}%[/{pct_color}]",
                st_badge,
            )
        console.print(table)


@pool_app.command("info")
def info_cmd(
    pool_name: Optional[str] = typer.Argument(None, help="指定存储池名称（如 sata11，默认展示全部）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看存储池详情 (`/zspool/info`) 与空间分类分布 (`/system/diskusage3`)."""
    with ZSpaceClient() as client:
        pool_data = _fetch_pool_payload(client)
        pool_list = pool_data.get("pool_list") or []
        if pool_name:
            pool_list = [
                p for p in pool_list if str(p.get("name")) == pool_name or str(p.get("id")) == pool_name
            ]

        usage_rows: list[dict[str, Any]] = []
        try:
            du_resp = client.disk_usage()
            usage_rows = _aggregate_disk_usage(du_resp)
            if pool_name:
                usage_rows = [r for r in usage_rows if r["pool"] == pool_name]
        except Exception:
            pass

        if json_output:
            console.print(
                json.dumps(
                    {"pools": pool_list, "disk_usage": usage_rows},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not pool_list:
            console.print(f"[yellow]未找到存储池: {pool_name or '(全部)'}[/yellow]")
            return

        for p in pool_list:
            total_b = _as_int(p.get("total_size"))
            free_b = _as_int(p.get("free_size"))
            used_b = _as_int(p.get("usage_size")) or max(0, total_b - free_b)
            pct = (used_b / total_b * 100.0) if total_b else 0.0
            usr_raid = (p.get("usr_raid_status") or {}).get("status") or "-"
            sys_raid = (p.get("sys_raid_status") or {}).get("status") or "-"

            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold cyan", justify="right")
            grid.add_column()
            grid.add_row("存储池名称:", f"[bold]{p.get('name')}[/bold] (ID: #{p.get('id')})")
            grid.add_row("RAID 类型:", f"[bold magenta]{str(p.get('pool_type', '')).upper()}[/bold magenta] (协议: {p.get('protocol')})")
            grid.add_row("阵列同步状态:", f"数据阵列: {usr_raid} | 系统阵列: {sys_raid}")
            grid.add_row("容量统计:", f"总计 [bold]{format_bytes(total_b)}[/bold] | 已用 [green]{format_bytes(used_b)}[/green] ({pct:.1f}%) | 可用 [cyan]{format_bytes(free_b)}[/cyan]")
            grid.add_row("安全冗余盘数:", f"{p.get('safe_hdd_count', 0)} 盘 | SSD 缓存: {'已启用' if p.get('has_cache') else '未启用'}")

            console.print(
                Panel(
                    grid,
                    title=f"[bold]存储池详情: {p.get('name')}[/bold]",
                    border_style="cyan",
                )
            )

        if usage_rows:
            du_table = Table(
                title="存储空间分类占用分布 (/system/diskusage3)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            du_table.add_column("存储池", style="bold")
            du_table.add_column("分类", style="cyan")
            du_table.add_column("归属账号")
            du_table.add_column("用途标签")
            du_table.add_column("物理占用", justify="right", style="bold green")

            for r in usage_rows:
                du_table.add_row(
                    r["pool"],
                    r["kind"],
                    r["owner"],
                    f"{r['label_cn']} [dim]({r['label']})[/dim]",
                    r["size_human"],
                )
            console.print(du_table)


# ── 2. 物理硬盘与 SMART 报告 (`zs pool disks` / `zs pool smart`) ──


@pool_app.command("disks")
def disks_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看设备硬盘插槽 (`/zspool/hardware/info`) 与所有物理硬盘健康状态."""
    with ZSpaceClient() as client:
        pool_data = _fetch_pool_payload(client)
        slots: dict[str, Any] = {}
        try:
            hw_resp = client.post("/zspool/hardware/info")
            slots = (hw_resp.get("data") or {}).get("slot") or {}
        except Exception:
            pass

        all_disks: list[dict[str, Any]] = []
        for p in pool_data.get("pool_list") or []:
            p_name = str(p.get("name") or "")
            for d in p.get("disk_list") or []:
                item = dict(d)
                item["pool_name"] = p_name
                all_disks.append(item)
        for fd in pool_data.get("free_list") or []:
            item = dict(fd)
            item["pool_name"] = "(未分配/空闲盘)"
            all_disks.append(item)

        if json_output:
            console.print(
                json.dumps(
                    {"hardware_slots": slots, "disks": all_disks},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if slots:
            slot_str = "  |  ".join(
                f"[bold]{k.upper()}[/bold] 插槽: {v} 个" for k, v in slots.items()
            )
            console.print(
                Panel(
                    f"硬件盘位规格: {slot_str}  (当前在位硬盘: [bold green]{len(all_disks)}[/bold green] 块)",
                    title="[bold]设备存储插槽信息 (/zspool/hardware/info)[/bold]",
                    border_style="cyan",
                )
            )

        table = Table(
            title="物理硬盘列表与基础健康状态",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("盘位", style="bold yellow")
        table.add_column("所属存储池", style="bold")
        table.add_column("型号 (Model)")
        table.add_column("序列号 (SN)", style="cyan")
        table.add_column("总线", width=6)
        table.add_column("总容量", justify="right")
        table.add_column("已用容量", justify="right")
        table.add_column("温度", justify="right")
        table.add_column("通电时间", justify="right")
        table.add_column("重映射扇区", justify="right")
        table.add_column("碎片率", justify="right")
        table.add_column("状态")

        for d in all_disks:
            smart = d.get("simple_smart") or {}
            frag = d.get("fs_fragment") or {}
            temp = _as_int(d.get("temp"))
            t_color = "red" if temp >= 50 else ("yellow" if temp >= 43 else "green")
            realloc = _as_int(smart.get("reallocated_sector_count"))
            frag_pct = float(frag.get("percentage") or 0.0) * 100.0
            ok_health = str(d.get("health")).lower() == "ok" and str(d.get("status")).lower() == "ok"
            st_text = f"[green]{d.get('errmsg') or '正常'}[/green]" if ok_health else f"[red]{d.get('errmsg') or d.get('health')}[/red]"

            table.add_row(
                f"盘位 {d.get('pos', '-')}",
                str(d.get("pool_name") or "-"),
                str(d.get("model") or "-"),
                str(d.get("sn") or "-"),
                str(d.get("dev_type") or "-"),
                format_bytes(_as_int(d.get("total_size"))),
                format_bytes(_as_int(d.get("usage_size"))),
                f"[{t_color}]{temp}°C[/{t_color}]",
                f"{_as_int(smart.get('power_on_hours')):,} h",
                "[green]0[/green]" if realloc == 0 else f"[red]{realloc}[/red]",
                f"{frag_pct:.2f}%",
                st_text,
            )
        console.print(table)
        console.print("[dim]提示: 运行 `zs pool smart <盘位号或SN>`（如 `zs pool smart 1`）可查看单盘完整 SMART 属性报告。[/dim]")


@pool_app.command("smart")
def smart_cmd(
    disk: Optional[str] = typer.Argument(
        None,
        help="硬盘盘位号（如 1, 2, 3）或硬盘序列号 SN（若不指定则默认展示第 1 块硬盘或全部硬盘的 SMART 报告）",
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出完整 SMART 属性"),
) -> None:
    """查看物理硬盘完整 SMART 健康详情报告 (`/zspool/smart/report2`)."""
    with ZSpaceClient() as client:
        pool_data = _fetch_pool_payload(client)
        all_disks: list[dict[str, Any]] = []
        for p in pool_data.get("pool_list") or []:
            for d in p.get("disk_list") or []:
                item = dict(d)
                item["pool_name"] = p.get("name", "")
                all_disks.append(item)

        if not all_disks:
            console.print("[yellow]未发现可查询 SMART 的物理硬盘。[/yellow]")
            return

        # 筛选目标硬盘
        targets: list[dict[str, Any]] = []
        if disk is None:
            targets = all_disks
        else:
            q = disk.strip().lower()
            for d in all_disks:
                if (
                    str(d.get("pos")).lower() == q
                    or f"盘位{d.get('pos')}" == q
                    or str(d.get("sn") or "").lower() == q
                    or q in str(d.get("model") or "").lower()
                ):
                    targets.append(d)
            if not targets:
                # 尝试直接把输入当作 SN 请求
                targets = [{"sn": disk.strip(), "pos": "?", "model": "未知型号", "pool_name": "-"}]

        reports: list[dict[str, Any]] = []
        for d in targets:
            sn = str(d.get("sn") or "")
            try:
                resp = client.post("/zspool/smart/report2", data={"sn": sn})
                rep_data = resp.get("data") or {}
            except ZSpaceError as exc:
                if not json_output:
                    console.print(f"[red]! 获取硬盘 {sn} SMART 报告失败:[/red] {exc}")
                continue
            reports.append(
                {
                    "pos": d.get("pos"),
                    "pool": d.get("pool_name"),
                    "model": d.get("model"),
                    "sn": sn,
                    "temp": d.get("temp"),
                    "health": rep_data.get("health", d.get("health")),
                    "attributes": rep_data.get("attributes") or [],
                }
            )

        if json_output:
            console.print(json.dumps(reports, ensure_ascii=False, indent=2))
            return

        for rep in reports:
            health_ok = str(rep["health"]).lower() == "ok"
            h_badge = "[bold green]健康 (OK)[/bold green]" if health_ok else f"[bold red]{rep['health']}[/bold red]"
            table = Table(
                title=(
                    f"硬盘 SMART 详细报告 — 盘位 {rep['pos']} | {rep['model']} "
                    f"(SN: {mask_secret(rep['sn'], 4, 4)}) | 状态: {h_badge}"
                ),
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("ID", justify="right", style="yellow", width=4)
            table.add_column("属性名称 (中文)", style="bold")
            table.add_column("英文属性名 (Attribute)", style="dim")
            table.add_column("当前值", justify="right")
            table.add_column("最差值", justify="right")
            table.add_column("临界值", justify="right")
            table.add_column("原始值 (Raw)", justify="right", style="bold cyan")
            table.add_column("状态", width=6)

            for attr in rep["attributes"]:
                a_ok = str(attr.get("health", "ok")).lower() == "ok"
                st_cell = "[green]正常[/green]" if a_ok else f"[red]{attr.get('health')}[/red]"
                table.add_row(
                    str(attr.get("id", "")),
                    str(attr.get("attr_name") or "-"),
                    str(attr.get("en_attr_name") or "-"),
                    str(attr.get("now_value", "")),
                    str(attr.get("worst", "")),
                    str(attr.get("thresh", "")),
                    str(attr.get("str_value") if attr.get("str_value") is not None else attr.get("raw_value", "")),
                    st_cell,
                )
            console.print(table)


# ── 3. 存储池快照管理 (`zs pool snapshot ls/create/delete`) ──


def _resolve_pool_id(client: ZSpaceClient, pool_id_or_name: str | int | None) -> tuple[int, str]:
    """根据存储池名称（如 sata11）或数字 ID（如 11）解析 (pid, pool_name)."""
    pool_data = _fetch_pool_payload(client)
    pools = pool_data.get("pool_list") or []
    if not pools:
        raise ZSpaceError("NO_POOL", "未发现任何可用存储池")

    if pool_id_or_name is None:
        first = pools[0]
        return _as_int(first.get("id")), str(first.get("name") or "")

    q = str(pool_id_or_name).strip()
    for p in pools:
        if str(p.get("id")) == q or str(p.get("name")) == q:
            return _as_int(p.get("id")), str(p.get("name") or "")

    if q.isdigit():
        return int(q), f"pool#{q}"
    raise ZSpaceError("POOL_NOT_FOUND", f"未找到存储池: {pool_id_or_name}")


@snapshot_app.command("ls")
def snapshot_ls_cmd(
    pool: Optional[str] = typer.Option(None, "--pool", "-P", help="存储池名称或 ID（默认第一个存储池，如 sata11）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出存储池快照列表 (`/zspool/snapshot/list`) 与定时计划状态 (`/zspool/snapshot/task/get`)."""
    with ZSpaceClient() as client:
        pid, p_name = _resolve_pool_id(client, pool)
        snap_resp = client.post("/zspool/snapshot/list", data={"pid": pid}, check_code=False)
        task_resp = client.post("/zspool/snapshot/task/get", data={"pid": pid}, check_code=False)

        snaps = (snap_resp.get("data") or {}).get("list") or []
        task_info = task_resp.get("data") or {}

        if json_output:
            console.print(
                json.dumps(
                    {"pool_id": pid, "pool_name": p_name, "snapshots": snaps, "task": task_info},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not snaps:
            console.print(f"[green]存储池 {p_name} (#{pid}) 当前暂无快照记录。[/green]")
            return

        table = Table(
            title=f"📸 存储池快照列表: {p_name} (#{pid}) - 共 {len(snaps)} 个快照",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("快照名称 / ID", style="bold yellow")
        table.add_column("备注 / 描述")
        table.add_column("创建时间", style="dim")
        table.add_column("占用大小", justify="right", style="green")

        for s in snaps:
            table.add_row(
                str(s.get("name") or s.get("id") or "-"),
                str(s.get("remark") or s.get("desc") or "-"),
                str(s.get("create_time") or s.get("itime") or "-"),
                format_bytes(_as_int(s.get("size"))),
            )
        console.print(table)


@snapshot_app.command("create")
def snapshot_create_cmd(
    name: str = typer.Argument(..., help="快照名称或备注描述"),
    pool: Optional[str] = typer.Option(None, "--pool", "-P", help="存储池名称或 ID（默认第一个存储池）"),
    remark: str = typer.Option("", "--remark", "-r", help="快照备注信息"),
) -> None:
    """创建存储池快照 (`/zspool/snapshot/create`)."""
    with ZSpaceClient() as client:
        pid, p_name = _resolve_pool_id(client, pool)
        try:
            client.post(
                "/zspool/snapshot/create",
                data={"pid": pid, "name": name, "remark": remark or name},
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 创建快照失败:[/red] {exc}")
            raise typer.Exit(1)

        console.print(f"[bold green]✔ 已在存储池 {p_name} (#{pid}) 创建快照:[/bold green] {name}")


@snapshot_app.command("delete")
def snapshot_delete_cmd(
    name: str = typer.Argument(..., help="要删除的快照名称或 ID"),
    pool: Optional[str] = typer.Option(None, "--pool", "-P", help="存储池名称或 ID（默认第一个存储池）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="免确认直接删除"),
) -> None:
    """删除存储池快照 (`/zspool/snapshot/delete`)."""
    if not yes and not typer.confirm(f"确定要删除快照 '{name}' 吗？"):
        console.print("[yellow]已取消操作。[/yellow]")
        raise typer.Exit(0)

    with ZSpaceClient() as client:
        pid, p_name = _resolve_pool_id(client, pool)
        try:
            client.post("/zspool/snapshot/delete", data={"pid": pid, "name": name, "names[]": [name]})
        except ZSpaceError as exc:
            console.print(f"[red]! 删除快照失败:[/red] {exc}")
            raise typer.Exit(1)

        console.print(f"[bold green]✔ 已从存储池 {p_name} (#{pid}) 删除快照:[/bold green] {name}")


# ── 4. SSD 缓存 (`zs pool cache`) 与外接存储 (`zs pool usb`) ──


@pool_app.command("cache")
def cache_cmd(
    pool: Optional[str] = typer.Option(None, "--pool", "-P", help="指定存储池名称或 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看 SSD 读写缓存配置与命中率 (`/zspool/info` cache_list & `/zspool/cache/hits`)."""
    with ZSpaceClient() as client:
        pool_data = _fetch_pool_payload(client)
        cache_list = pool_data.get("cache_list") or []
        pid, p_name = _resolve_pool_id(client, pool)

        hits_data: dict[str, Any] = {}
        try:
            hits_resp = client.post("/zspool/cache/hits", data={"pid": pid}, check_code=False)
            if str(hits_resp.get("code")) == "200":
                hits_data = hits_resp.get("data") or {}
        except Exception:
            pass

        if json_output:
            console.print(
                json.dumps(
                    {"pool_id": pid, "pool_name": p_name, "cache_list": cache_list, "hits": hits_data},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not cache_list:
            console.print(f"[green]存储池 {p_name} (#{pid}) 当前未挂载 SSD 加速缓存盘。[/green]")
            return

        table = Table(title="🚀 SSD 缓存盘列表", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("缓存名称", style="bold")
        table.add_column("关联池")
        table.add_column("总容量", justify="right")
        table.add_column("状态")
        for c in cache_list:
            table.add_row(
                str(c.get("name") or "-"),
                str(c.get("pool_name") or c.get("pid") or p_name),
                format_bytes(_as_int(c.get("total_size"))),
                str(c.get("status") or "ok"),
            )
        console.print(table)


@pool_app.command("usb")
def usb_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看外接 USB / 虚拟 USB (`/zspool/vusb/status`) 与外置挂载设备 (`ext_mnt_list`)."""
    with ZSpaceClient() as client:
        pool_data = _fetch_pool_payload(client)
        ext_list = pool_data.get("ext_mnt_list") or []
        vusb_data: dict[str, Any] = {}
        try:
            vusb_resp = client.post("/zspool/vusb/status", check_code=False)
            vusb_data = vusb_resp.get("data") or {}
        except Exception:
            pass

        if json_output:
            console.print(
                json.dumps(
                    {"ext_mnt_list": ext_list, "vusb": vusb_data},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        v_status = str(vusb_data.get("status") or "none")
        usb_drive = vusb_data.get("usb_flash_drive") or {}
        console.print(
            Panel(
                f"虚拟 USB 状态: [bold cyan]{v_status}[/bold cyan]  |  "
                f"移动端模式: {'开启' if vusb_data.get('mobile_mode') else '关闭'}  |  "
                f"挂载点: {usb_drive.get('mountpoint') or '无'}",
                title="[bold]虚拟 USB 服务状态 (/zspool/vusb/status)[/bold]",
                border_style="cyan",
            )
        )

        if not ext_list:
            console.print("[dim]当前未检测到已挂载的外置 USB / eSATA 移动硬盘。[/dim]")
            return

        table = Table(title="🔌 外置 USB / eSATA 存储设备列表", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("设备名称", style="bold")
        table.add_column("类型", style="yellow")
        table.add_column("文件系统")
        table.add_column("总容量", justify="right")
        table.add_column("可用空间", justify="right")
        table.add_column("挂载路径", style="green")

        for ext in ext_list:
            table.add_row(
                str(ext.get("name") or ext.get("label") or "-"),
                str(ext.get("dev_type") or ext.get("type") or "USB"),
                str(ext.get("fs_type") or ext.get("fs") or "-"),
                format_bytes(_as_int(ext.get("total_size"))),
                format_bytes(_as_int(ext.get("free_size"))),
                str(ext.get("mnt") or ext.get("path") or "-"),
            )
        console.print(table)
