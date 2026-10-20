"""极空间 `zs safe` 私人保险箱与加密文件夹管理命令组.

涵盖 API 模块：
- 模块 97275：私人保险箱 (`/v2/file/safe/status`, `/v2/file/safe/open`, `/v2/file/safe/close`,
  `/v2/file/safe/tips`, `/v2/file/safe/init`, `/v2/file/safe/dwstatus`)
- 模块 5219：加密文件夹 (`/v2/encryptdir/unlock`, `/v2/encryptdir/lock`, `/v2/encryptdir/new`)
"""

from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands.file_cmd import fetch_dir_list, file_icon, format_size, format_ts
from zspace_cli.core.client import ZSpaceClient, ZSpaceError
from zspace_cli.core.crypto import rsa_encrypt

safe_app = typer.Typer(
    name="safe",
    help="私人保险箱 (Safebox) 与加密文件夹状态查询、RSA 解锁、锁定及浏览管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
# 导出别名供 main.py 自动发现
app = safe_app
console = Console()

SAFE_STATUS_MAP: dict[int, str] = {
    1: "未启用 (尚未初始化创建保险箱)",
    2: "已锁定 (需输入保险箱密码解锁)",
    3: "正在重置中...",
    4: "已解锁开启 (可正常读写)",
}


def _get_nas_pubkey(client: ZSpaceClient) -> str:
    """获取用于加密保险箱密码的 NAS 2048-bit RSA 公钥."""
    if client.config.nas_pub_key:
        return client.config.nas_pub_key
    pub_info = client.get_public_key()
    key = str(pub_info.get("key") or "")
    if not key:
        raise ZSpaceError("NO_PUBKEY", "无法获取 NAS RSA 公钥 (/key/pub)")
    return key


@safe_app.command("status")
def status_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看私人保险箱当前状态 (`/v2/file/safe/status`)、密码提示与双备份信息."""
    with ZSpaceClient() as client:
        st_resp = client.post("/v2/file/safe/status", check_code=False)
        st_data = st_resp.get("data") or {}
        status_code = int(st_data.get("status") or 0)
        pool_name = str(st_data.get("pool") or "")

        tips_str = ""
        if status_code in (2, 4):
            try:
                t_resp = client.post("/v2/file/safe/tips", check_code=False)
                tips_str = str((t_resp.get("data") or {}).get("tips") or "")
            except Exception:
                pass

        dw_data: dict[str, Any] = {}
        if status_code == 4:
            try:
                dw_resp = client.post("/v2/file/safe/dwstatus", check_code=False)
                dw_data = dw_resp.get("data") or {}
            except Exception:
                pass

        if json_output:
            console.print(
                json.dumps(
                    {
                        "status": status_code,
                        "status_text": SAFE_STATUS_MAP.get(status_code, f"未知({status_code})"),
                        "pool": pool_name,
                        "tips": tips_str,
                        "double_write": dw_data or None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        badge_color = "green" if status_code == 4 else ("yellow" if status_code == 2 else "cyan")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column()
        grid.add_row(
            "保险箱状态:",
            f"[bold {badge_color}]{SAFE_STATUS_MAP.get(status_code, f'状态 {status_code}')}[/bold {badge_color}]",
        )
        grid.add_row("所在存储池:", pool_name or "(未绑定)")
        if tips_str:
            grid.add_row("密码提示语:", tips_str)
        if dw_data:
            grid.add_row("ZDR 双备份状态:", str(dw_data.get("double_write_status", "-")))

        console.print(
            Panel(
                grid,
                title="[bold]🔒 极空间私人保险箱状态 (/v2/file/safe/status)[/bold]",
                border_style=badge_color,
            )
        )


@safe_app.command("open")
def open_cmd(
    password: Optional[str] = typer.Option(
        None,
        "--password",
        "-p",
        prompt=True,
        hide_input=True,
        help="保险箱或加密目录密码（使用 RSA 公钥加密后传输）",
    ),
    encrypt_dir: Optional[str] = typer.Option(
        None,
        "--dir",
        "-d",
        help="若指定加密文件夹路径，则调用 /v2/encryptdir/unlock 解锁该目录",
    ),
) -> None:
    """使用 RSA 加密密码解锁私人保险箱 (`/v2/file/safe/open`) 或加密文件夹 (`/v2/encryptdir/unlock`)."""
    with ZSpaceClient() as client:
        pub_key = _get_nas_pubkey(client)
        enc_pwd = rsa_encrypt((password or "").strip(), pub_key)

        try:
            if encrypt_dir:
                client.post("/v2/encryptdir/unlock", data={"path": encrypt_dir, "password": enc_pwd})
                console.print(f"[bold green]🔓 已成功解锁加密文件夹:[/bold green] {encrypt_dir}")
                return

            resp = client.post("/v2/file/safe/open", data={"password": enc_pwd})
            pool_name = (resp.get("data") or {}).get("pool") or "sata11"
            console.print(
                f"[bold green]🔓 私人保险箱已成功解锁！[/bold green] "
                f"(路径: [cyan]/{pool_name}/safe/my[/cyan]，可使用 `zs safe ls` 浏览)"
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 解锁失败:[/red] {exc}")
            raise typer.Exit(1)


@safe_app.command("close")
def close_cmd(
    encrypt_dir: Optional[str] = typer.Option(
        None,
        "--dir",
        "-d",
        help="若指定加密文件夹路径，则调用 /v2/encryptdir/lock 锁定该目录",
    ),
) -> None:
    """锁定关闭私人保险箱 (`/v2/file/safe/close`) 或加密文件夹 (`/v2/encryptdir/lock`)."""
    with ZSpaceClient() as client:
        try:
            if encrypt_dir:
                client.post("/v2/encryptdir/lock", data={"path": encrypt_dir})
                console.print(f"[bold green]🔒 已锁定加密文件夹:[/bold green] {encrypt_dir}")
                return

            client.post("/v2/file/safe/close")
            console.print("[bold green]🔒 私人保险箱已安全锁定关闭。[/bold green]")
        except ZSpaceError as exc:
            console.print(f"[red]! 锁定失败:[/red] {exc}")
            raise typer.Exit(1)


@safe_app.command("ls")
def ls_cmd(
    subpath: Optional[str] = typer.Argument(None, help="保险箱内子目录相对路径（默认根目录）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """浏览已解锁的私人保险箱目录内容."""
    with ZSpaceClient() as client:
        st_resp = client.post("/v2/file/safe/status", check_code=False)
        st_data = st_resp.get("data") or {}
        status_code = int(st_data.get("status") or 0)
        pool_name = str(st_data.get("pool") or "sata11")

        if status_code == 1:
            console.print("[yellow]当前账号尚未初始化开启私人保险箱。[/yellow]")
            return
        if status_code != 4:
            console.print("[yellow]🔒 私人保险箱当前处于锁定状态，请先运行 `zs safe open` 解锁。[/yellow]")
            raise typer.Exit(1)

        base_safe_path = f"/{pool_name}/safe/my"
        target_path = f"{base_safe_path}/{subpath.strip('/')}" if subpath else base_safe_path

        try:
            items, total = fetch_dir_list(client, target_path, show_hidden=True, limit=200)
        except ZSpaceError as exc:
            console.print(f"[red]! 读取保险箱目录失败:[/red] {exc}")
            raise typer.Exit(1)

        if json_output:
            console.print(
                json.dumps(
                    {"path": target_path, "total": total, "list": items},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        table = Table(
            title=f"🔐 保险箱目录: [bold cyan]{target_path}[/bold cyan] (共 {total} 项)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("类型", width=9)
        table.add_column("名称", style="bold")
        table.add_column("大小", justify="right", width=11)
        table.add_column("修改时间", width=17, style="dim")
        table.add_column("路径", style="dim")

        for it in items:
            is_dir = str(it.get("is_dir")) == "1"
            sz = "-" if is_dir else format_size(int(it.get("size") or 0))
            table.add_row(
                file_icon(it),
                str(it.get("name") or ""),
                sz,
                format_ts(it.get("modify_time")),
                str(it.get("path") or ""),
            )
        console.print(table)
