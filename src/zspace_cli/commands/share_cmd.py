"""极空间 `zs share` 外链分享、闪电传与内部分享管理命令组.

涵盖 API 模块：
- 模块 74168 & 5219：外链分享与闪电传列表 (`/v2/share/list`)、统计 (`/v2/share/statics`)、
  创建外链分享 (`/v2/share/create`)、按 ID 删除 (`/v2/share/delete`)、按状态批量清理 (`/v2/share/delete/by`)、
  修改分享状态 (`/v2/share/modify`)
- 模块 5219：NAS 内部成员共享 (`/v2/nshare/mine`, `/v2/nshare/forme`, `/v2/nshare/create`, `/v2/nshare/cancel`)
"""

from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands.file_cmd import format_ts
from zspace_cli.core.client import ZSpaceClient, ZSpaceError

share_app = typer.Typer(
    name="share",
    help="外链分享、极空间闪电传与 NAS 内部共享管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
# 导出别名供 main.py 自动发现
app = share_app
console = Console()


def _as_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _share_state_badge(item: dict[str, Any]) -> str:
    """根据外链分享条目的 state 与 expired 字段生成状态标签."""
    state = str(item.get("state") or "")
    expired = str(item.get("expired") or "0") == "1"
    remain = _as_int(item.get("remain"))
    if state == "0":
        return "[dim]已取消[/dim]"
    if expired or state == "3":
        return "[yellow]已过期[/yellow]"
    if state == "1" and remain > 0:
        return "[bold green]有效分享中[/bold green]"
    if state == "1" and remain == 0:
        return "[yellow]次数已用尽[/yellow]"
    return f"状态({state})"


@share_app.command("ls")
def ls_cmd(
    share_type: str = typer.Option(
        "all",
        "--type",
        "-t",
        help="分享类型过滤: all (全部) / link (外链分享 type=2) / flash (闪电传 type=1) / internal (内部共享)",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="最大返回条数"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出外链分享 (`/v2/share/list`)、闪电传与 NAS 内部共享 (`/v2/nshare/*`) 及统计概览."""
    mode = share_type.strip().lower()
    with ZSpaceClient() as client:
        stats: dict[str, Any] = {}
        link_shares: list[dict[str, Any]] = []
        flash_shares: list[dict[str, Any]] = []
        internal_mine: list[dict[str, Any]] = []
        internal_forme: list[dict[str, Any]] = []

        if mode in ("all", "link", "flash"):
            try:
                st_resp = client.post("/v2/share/statics", check_code=False)
                stats = st_resp.get("data") or {}
            except Exception:
                pass

        if mode in ("all", "link"):
            try:
                r2 = client.post("/v2/share/list", data={"start": 0, "num": limit, "type": 2}, check_code=False)
                link_shares = (r2.get("data") or {}).get("list") or []
            except Exception:
                pass

        if mode in ("all", "flash"):
            try:
                r1 = client.post("/v2/share/list", data={"start": 0, "num": limit, "type": 1}, check_code=False)
                flash_shares = (r1.get("data") or {}).get("list") or []
            except Exception:
                pass

        if mode in ("all", "internal"):
            try:
                rm = client.post("/v2/nshare/mine", data={"start": 0, "num": limit}, check_code=False)
                internal_mine = (rm.get("data") or {}).get("list") or []
                rf = client.post("/v2/nshare/forme", data={"sortby": "time", "order": "desc"}, check_code=False)
                internal_forme = (rf.get("data") or {}).get("list") or []
            except Exception:
                pass

        if json_output:
            console.print(
                json.dumps(
                    {
                        "statics": stats,
                        "link_shares": link_shares,
                        "flash_shares": flash_shares,
                        "internal_mine": internal_mine,
                        "internal_forme": internal_forme,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if stats:
            console.print(
                Panel(
                    f"外链总数: [bold]{stats.get('total', 0)}[/bold]  |  "
                    f"生效中: [bold green]{stats.get('normal', 0)}[/bold green]  |  "
                    f"已过期: [yellow]{stats.get('expired', 0)}[/yellow]  |  "
                    f"已取消: [dim]{stats.get('cancel', 0)}[/dim]",
                    title="[bold]🔗 极空间分享中心统计 (/v2/share/statics)[/bold]",
                    border_style="cyan",
                )
            )

        all_external = link_shares + flash_shares
        if all_external:
            table = Table(
                title=f"外链分享与闪电传列表 (共 {len(all_external)} 条)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("ID", justify="right", style="bold yellow", width=5)
            table.add_column("类型", width=8)
            table.add_column("分享内容名称", style="bold")
            table.add_column("提取码", style="magenta", width=8)
            table.add_column("剩余次数", justify="right", width=8)
            table.add_column("状态", width=12)
            table.add_column("创建时间", width=16, style="dim")
            table.add_column("源目录路径", style="dim")

            for s in all_external:
                s_type = "闪电传" if str(s.get("type")) == "1" else "公开外链"
                title_name = str(s.get("name") or s.get("fname") or s.get("dirname") or "-")
                table.add_row(
                    str(s.get("id") or ""),
                    s_type,
                    title_name,
                    str(s.get("pass") or "无密码"),
                    str(s.get("remain") or "0"),
                    _share_state_badge(s),
                    format_ts(s.get("created_at")),
                    str(s.get("real_path") or "-"),
                )
            console.print(table)
        elif mode in ("link", "flash"):
            console.print("[yellow]当前没有匹配的分享记录。[/yellow]")

        if internal_mine or internal_forme:
            n_table = Table(
                title="NAS 内部账号共享列表 (/v2/nshare/*)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            n_table.add_column("方向", width=10, style="cyan")
            n_table.add_column("名称", style="bold")
            n_table.add_column("共享路径")
            for item in internal_mine:
                n_table.add_row("我共享的", str(item.get("name") or "-"), str(item.get("path") or "-"))
            for item in internal_forme:
                n_table.add_row("共享给我", str(item.get("name") or "-"), str(item.get("path") or "-"))
            console.print(n_table)


@share_app.command("create")
def create_cmd(
    paths: list[str] = typer.Argument(..., help="要分享的一个或多个 NAS 文件/目录路径"),
    days: int = typer.Option(5, "--days", "-d", help="有效期天数（默认 5 天，转为秒数）"),
    password: bool = typer.Option(True, "--password/--no-password", help="是否开启提取码保护"),
    snum: int = typer.Option(1000, "--snum", "-n", help="最大允许访问/下载次数"),
    flash: bool = typer.Option(False, "--flash", help="创建为极空间闪电传口令 (type=1，默认为外链分享 type=2)"),
    internal: bool = typer.Option(False, "--internal", help="创建为 NAS 内部共享文件夹 (/v2/nshare/create)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """创建外链分享 (`/v2/share/create`)、闪电传或内部共享 (`/v2/nshare/create`)."""
    with ZSpaceClient() as client:
        if internal:
            results: list[Any] = []
            for p in paths:
                try:
                    resp = client.post("/v2/nshare/create", data={"path": p})
                    results.append(resp.get("data") or resp)
                except ZSpaceError as exc:
                    console.print(f"[red]! 创建内部共享失败 ({p}):[/red] {exc}")
                    raise typer.Exit(1)
            if json_output:
                console.print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                console.print(f"[bold green]✔ 已成功设置内部共享:[/bold green] {', '.join(paths)}")
            return

        expire_sec = max(1, days) * 86400
        req_data: dict[str, Any] = {
            "paths[]": list(paths),
            "expire": str(expire_sec),
            "has_pass": "1" if password else "0",
            "snum": str(snum),
            "type": "1" if flash else "2",
            "show_hidden": "0",
        }
        try:
            resp = client.post("/v2/share/create", data=req_data)
        except ZSpaceError as exc:
            console.print(f"[red]! 创建外链分享失败:[/red] {exc}")
            raise typer.Exit(1)

        data = resp.get("data") or {}
        share_info = data.get("share") or data
        share_code = str(share_info.get("code") or "")
        share_pass = str(share_info.get("pass") or "")

        if json_output:
            console.print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column()
        grid.add_row("分享模式:", "极空间闪电传 (type=1)" if flash else "公开外链分享 (type=2)")
        grid.add_row("包含路径:", ", ".join(paths))
        grid.add_row("分享标识码 (Code):", f"[bold green]{share_code}[/bold green]")
        grid.add_row("提取密码 (Pass):", f"[bold magenta]{share_pass or '(无密码)'}[/bold magenta]")
        grid.add_row("有效期 / 次数:", f"{days} 天 / {snum} 次")

        console.print(
            Panel(
                grid,
                title="[bold green]✔ 分享创建成功！[/bold green]",
                border_style="green",
            )
        )


@share_app.command("rm")
def rm_cmd(
    ids: Optional[list[str]] = typer.Argument(None, help="要删除的分享记录 ID 列表（见 `zs share ls` 的 ID 列）"),
    code: Optional[str] = typer.Option(None, "--code", "-c", help="按分享标识码 code 取消分享 (/v2/share/modify)"),
    clear_expired: bool = typer.Option(False, "--clear-expired", help="一键清理所有已过期的外链分享 (/v2/share/delete/by)"),
    internal_path: Optional[str] = typer.Option(None, "--internal-path", help="取消指定的 NAS 内部共享路径 (/v2/nshare/cancel)"),
) -> None:
    """删除或取消外链分享 (`/v2/share/delete`, `/v2/share/modify`, `/v2/share/delete/by`) 及内部共享."""
    with ZSpaceClient() as client:
        try:
            if internal_path:
                client.post("/v2/nshare/cancel", data={"path": internal_path})
                console.print(f"[bold green]✔ 已取消内部共享:[/bold green] {internal_path}")
                return

            if clear_expired:
                client.post("/v2/share/delete/by", data={"stype": 0, "state": 3})
                console.print("[bold green]✔ 已清理所有已过期的外链分享记录。[/bold green]")
                return

            if code:
                client.post("/v2/share/modify", data={"code": code, "state": 0})
                console.print(f"[bold green]✔ 已取消外链分享:[/bold green] {code}")
                return

            if not ids:
                console.print("[red]! 请指定要删除的分享 ID，或使用 `--clear-expired` / `--code` 参数。[/red]")
                raise typer.Exit(1)

            client.post("/v2/share/delete", data={"ids[]": list(ids)})
            console.print(f"[bold green]✔ 已删除外链分享记录 ID:[/bold green] {', '.join(ids)}")
        except ZSpaceError as exc:
            console.print(f"[red]! 删除/取消分享失败:[/red] {exc}")
            raise typer.Exit(1)
