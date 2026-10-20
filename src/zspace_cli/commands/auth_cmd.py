"""极空间 `zs auth` 子命令模块：连接状态诊断、脱敏会话详情与 RSA 独立登录."""

from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.core.client import ZSpaceClient, ZSpaceError
from zspace_cli.core.config import load_config, mask_secret

auth_app = typer.Typer(
    name="auth",
    help="账号鉴权、本地代理/局域网连接诊断与独立登录管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# /system/diskusage3 空间分类标签中文映射
USAGE_LABELS_CN: dict[str, str] = {
    "my": "个人文件",
    "my_tm": "Time Machine 备份",
    "my_recycle": "个人回收站",
    "my_safe": "私人保险箱",
    "pub_recycle": "公共回收站",
    "sys_raid": "池/RAID 元数据",
    "sys_docker": "Docker 容器与镜像",
    "sys_vm": "虚拟机 (VM)",
    "sys_iscsi": "iSCSI 存储",
    "sys_other": "其他系统数据",
}


def format_bytes(num_bytes: int | float) -> str:
    """将字节数格式化为人类可读的容量字符串 (KB/MB/GB/TB)."""
    val = float(num_bytes or 0)
    if val >= 1024**4:
        return f"{val / (1024**4):.2f} TB"
    if val >= 1024**3:
        return f"{val / (1024**3):.2f} GB"
    if val >= 1024**2:
        return f"{val / (1024**2):.1f} MB"
    if val >= 1024:
        return f"{val / 1024:.1f} KB"
    return f"{int(val)} B"


def _as_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _aggregate_disk_usage(usage_resp: dict[str, Any]) -> list[dict[str, Any]]:
    """聚合 /system/diskusage3 各物理盘的分类空间占用."""
    data = usage_resp.get("data") or {}
    rows_by_pool: dict[str, dict[tuple[str, str, str], int]] = {}

    for disk in data.get("disk_usage") or []:
        pool_name = str(disk.get("pool_name") or disk.get("pool_key") or "默认存储池")
        acc = rows_by_pool.setdefault(pool_name, {})

        def _add(kind: str, label: str, size: int, owner: str = "") -> None:
            if size > 0:
                key = (kind, owner, label)
                acc[key] = acc.get(key, 0) + size

        usage_v2 = disk.get("usage_v2") or {}
        for u in usage_v2.get("user") or []:
            owner = str(u.get("remark") or u.get("nickname") or u.get("username") or "用户")
            if u.get("is_master"):
                owner += " (管理员)"
            for item in u.get("list") or []:
                _add("个人/用户", str(item.get("label") or ""), _as_int(item.get("phy_size")), owner)

        pub_rec = usage_v2.get("public_recycle")
        if isinstance(pub_rec, dict):
            _add("公共", str(pub_rec.get("label") or "pub_recycle"), _as_int(pub_rec.get("phy_size")), "共享空间")

        for item in usage_v2.get("public") or []:
            _add("公共", str(item.get("label") or ""), _as_int(item.get("phy_size")), "共享空间")

        for item in usage_v2.get("sys") or []:
            _add("系统", str(item.get("label") or ""), _as_int(item.get("phy_size")), "NAS 系统")

    summary_rows: list[dict[str, Any]] = []
    for pool_name, acc in rows_by_pool.items():
        sorted_items = sorted(acc.items(), key=lambda x: -x[1])
        for (kind, owner, label), size in sorted_items:
            summary_rows.append(
                {
                    "pool": pool_name,
                    "kind": kind,
                    "owner": owner,
                    "label": label,
                    "label_cn": USAGE_LABELS_CN.get(label, label),
                    "size": size,
                    "size_human": format_bytes(size),
                }
            )
    return summary_rows


@auth_app.command("check")
def check_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出完整诊断结果"),
) -> None:
    """验证当前连接状态，展示登录用户、网关状态、存储池 (/zspool/info) 与磁盘空间 (/system/diskusage3) 概览."""
    with ZSpaceClient() as client:
        cfg = client.config
        probes = client.check_connectivity()
        proxy_probe = probes.get("proxy")
        lan_probe = probes.get("lan")

        any_reachable = bool(
            (proxy_probe and proxy_probe.reachable) or (lan_probe and lan_probe.reachable)
        )

        # 获取存储池与磁盘空间数据
        pools_data: list[dict[str, Any]] = []
        disks_data: list[dict[str, Any]] = []
        usage_rows: list[dict[str, Any]] = []
        api_error: str = ""

        if any_reachable:
            try:
                pool_resp = client.pool_info()
                raw_pools = (pool_resp.get("data") or {}).get("pool_list") or []
                for p in raw_pools:
                    total_b = _as_int(p.get("total_size"))
                    free_b = _as_int(p.get("free_size"))
                    used_b = _as_int(p.get("usage_size")) or max(0, total_b - free_b)
                    used_pct = (used_b / total_b * 100.0) if total_b else 0.0
                    pools_data.append(
                        {
                            "id": p.get("id", ""),
                            "name": p.get("name", ""),
                            "pool_type": str(p.get("pool_type") or p.get("stripe_mode") or "").upper(),
                            "protocol": p.get("protocol", ""),
                            "status": p.get("status", ""),
                            "total_size": total_b,
                            "used_size": used_b,
                            "free_size": free_b,
                            "used_pct": round(used_pct, 1),
                            "disk_count": len(p.get("disk_list") or []),
                        }
                    )
                    for d in p.get("disk_list") or []:
                        smart = d.get("simple_smart") or {}
                        disks_data.append(
                            {
                                "pool": p.get("name", ""),
                                "pos": d.get("pos", ""),
                                "model": d.get("model", ""),
                                "sn": mask_secret(str(d.get("sn") or ""), 3, 3),
                                "dev_type": d.get("dev_type", ""),
                                "temp": _as_int(d.get("temp")),
                                "health": d.get("health", ""),
                                "status": d.get("status", ""),
                                "errmsg": d.get("errmsg", ""),
                                "total_size": _as_int(d.get("total_size")),
                                "usage_size": _as_int(d.get("usage_size")),
                                "power_on_hours": _as_int(smart.get("power_on_hours")),
                            }
                        )
            except Exception as exc:
                api_error = str(exc)

            try:
                du_resp = client.disk_usage()
                usage_rows = _aggregate_disk_usage(du_resp)
            except Exception:
                pass

        if json_output:
            out = {
                "ok": any_reachable and not api_error,
                "user": {
                    "username": cfg.username,
                    "nickname": cfg.nickname,
                    "is_master": bool(cfg.is_master),
                },
                "device": {
                    "nas_name": cfg.nas_name,
                    "nas_id": cfg.nas_id,
                    "device_mode": cfg.device_mode,
                    "client_device": cfg.device,
                    "device_id": cfg.device_id,
                },
                "connectivity": {
                    k: {
                        "url": v.url,
                        "reachable": v.reachable,
                        "latency_ms": round(v.latency_ms, 1),
                        "detail": v.detail,
                    }
                    for k, v in probes.items()
                },
                "active_gateway": client.base_url,
                "pools": pools_data,
                "disks": disks_data,
                "disk_usage": usage_rows,
                "error": api_error,
            }
            console.print(json.dumps(out, ensure_ascii=False, indent=2))
            if not any_reachable:
                raise typer.Exit(1)
            return

        # 1. 顶部身份与连接状态面板
        status_table = Table.grid(padding=(0, 2))
        status_table.add_column(style="bold cyan", justify="right")
        status_table.add_column()

        user_label = cfg.username or "(未知用户)"
        if cfg.nickname and cfg.nickname != cfg.username:
            user_label += f" ({cfg.nickname})"
        if cfg.is_master:
            user_label += " [bold green][管理员][/bold green]"

        nas_label = f"[bold]{cfg.nas_name or '极空间 NAS'}[/bold]"
        if cfg.device_mode:
            nas_label += f" ({cfg.device_mode})"

        status_table.add_row("登录账号:", user_label)
        status_table.add_row("目标设备:", f"{nas_label}  [dim]NAS ID: {cfg.nas_id}[/dim]")
        status_table.add_row("客户端名:", f"{cfg.device}  [dim]Device ID: {mask_secret(cfg.device_id, 8, 6)}[/dim]")

        if proxy_probe:
            p_badge = (
                f"[bold green]● 在线[/bold green] ({proxy_probe.latency_ms:.1f} ms) - {proxy_probe.url}"
                if proxy_probe.reachable
                else f"[bold red]○ 离线[/bold red] - {proxy_probe.url} ({proxy_probe.detail})"
            )
            status_table.add_row("本地代理:", p_badge)

        if lan_probe:
            l_badge = (
                f"[bold green]● 可达[/bold green] ({lan_probe.latency_ms:.1f} ms) - {lan_probe.url}"
                if lan_probe.reachable
                else f"[yellow]○ 未连通[/yellow] - {lan_probe.url} ({lan_probe.detail})"
            )
            status_table.add_row("局域网直连:", l_badge)

        status_table.add_row("当前激活网关:", f"[bold underline]{client.base_url}[/bold underline]")

        console.print(
            Panel(
                status_table,
                title="[bold]极空间 (ZSpace) 连接与鉴权状态[/bold]",
                border_style="green" if any_reachable and not api_error else "red",
            )
        )

        if not any_reachable:
            console.print("[red]! 无法连接到极空间本地代理或局域网地址，请确认桌面客户端已运行或检查网络。[/red]")
            raise typer.Exit(1)

        if api_error:
            console.print(f"[red]! 鉴权或业务接口调用异常: {api_error}[/red]")
            raise typer.Exit(1)

        # 2. 存储池 (/zspool/info) 概览表格
        if pools_data:
            pool_table = Table(
                title="存储池与容量概览 (/zspool/info)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            pool_table.add_column("存储池", style="bold")
            pool_table.add_column("RAID/模式", style="magenta")
            pool_table.add_column("盘数", justify="right")
            pool_table.add_column("状态")
            pool_table.add_column("总容量", justify="right")
            pool_table.add_column("已用空间", justify="right")
            pool_table.add_column("可用空间", justify="right")
            pool_table.add_column("使用率", justify="right")

            for p in pools_data:
                pct = p["used_pct"]
                pct_color = "red" if pct >= 85 else ("yellow" if pct >= 70 else "green")
                health_ok = str(p["status"]).lower() in ("normal", "ok", "1", "")
                st_str = "[green]正常[/green]" if health_ok else f"[yellow]{p['status']}[/yellow]"
                pool_table.add_row(
                    f"{p['name']} [dim](#{p['id']})[/dim]",
                    str(p["pool_type"] or "-"),
                    f"{p['disk_count']} 盘",
                    st_str,
                    format_bytes(p["total_size"]),
                    format_bytes(p["used_size"]),
                    format_bytes(p["free_size"]),
                    f"[{pct_color}]{pct:.1f}%[/{pct_color}]",
                )
            console.print(pool_table)

        if disks_data:
            disk_table = Table(
                title="物理硬盘 SMART 与温度状态",
                box=box.SIMPLE,
                header_style="bold cyan",
            )
            disk_table.add_column("盘位", style="bold")
            disk_table.add_column("所属池")
            disk_table.add_column("硬盘型号")
            disk_table.add_column("接口", style="dim")
            disk_table.add_column("物理容量", justify="right")
            disk_table.add_column("已分配", justify="right")
            disk_table.add_column("温度", justify="right")
            disk_table.add_column("通电时长", justify="right")
            disk_table.add_column("健康状态")

            for d in disks_data:
                temp = d["temp"]
                t_color = "red" if temp >= 50 else ("yellow" if temp >= 43 else "green")
                ok_flag = str(d["health"]).lower() == "ok" and str(d["status"]).lower() == "ok"
                h_str = f"[green]{d['errmsg'] or '正常'}[/green]" if ok_flag else f"[red]{d['errmsg'] or d['health']}[/red]"
                disk_table.add_row(
                    f"盘位 {d['pos']}",
                    d["pool"],
                    d["model"],
                    d["dev_type"],
                    format_bytes(d["total_size"]),
                    format_bytes(d["usage_size"]),
                    f"[{t_color}]{temp}°C[/{t_color}]",
                    f"{d['power_on_hours']} h",
                    h_str,
                )
            console.print(disk_table)

        # 3. 磁盘空间分类占用 (/system/diskusage3) 概览表格
        if usage_rows:
            du_table = Table(
                title="磁盘空间分类占用明细 (/system/diskusage3)",
                box=box.SIMPLE_HEAVY,
                header_style="bold cyan",
            )
            du_table.add_column("存储池", style="bold")
            du_table.add_column("分类", style="cyan")
            du_table.add_column("归属者")
            du_table.add_column("空间项目")
            du_table.add_column("物理占用", justify="right", style="bold green")

            for row in usage_rows[:12]:
                du_table.add_row(
                    row["pool"],
                    row["kind"],
                    row["owner"],
                    f"{row['label_cn']} [dim]({row['label']})[/dim]",
                    row["size_human"],
                )
            console.print(du_table)


@auth_app.command("info")
def info_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出脱敏会话配置"),
) -> None:
    """展示脱敏后的当前会话凭证与本地配置文件路径详情."""
    cfg = load_config()
    info_dict = {
        "vuex_path": str(cfg.vuex_path) if cfg.vuex_path else None,
        "conf_path": str(cfg.conf_path) if cfg.conf_path else None,
        "cli_session_path": str(cfg.cli_session_path) if cfg.cli_session_path else None,
        "username": cfg.username,
        "nickname": cfg.nickname,
        "is_master": bool(cfg.is_master),
        "nas_name": cfg.nas_name,
        "nas_id": mask_secret(cfg.nas_id, 4, 3) if cfg.nas_id else "",
        "device_mode": cfg.device_mode,
        "device_pdt": cfg.device_pdt,
        "disk_num": cfg.disk_num,
        "client_device": cfg.device,
        "device_id": mask_secret(cfg.device_id, 6, 6),
        "token_masked": mask_secret(cfg.token, 6, 4),
        "sign_masked": mask_secret(cfg.sign, 6, 4),
        "local_port": cfg.local_port,
        "proxy_base_url": cfg.proxy_base_url,
        "lan_ip": cfg.lan_ip or "(未探测到)",
        "lan_base_url": cfg.lan_base_url or "(无)",
        "active_base_url": cfg.base_url,
        "web_version": cfg.web_version,
        "app_version": cfg.app_version,
        "player_port": cfg.player_port,
        "mount_volume": cfg.mount_volume,
    }

    if json_output:
        console.print(json.dumps(info_dict, ensure_ascii=False, indent=2))
        return

    table = Table(
        title="极空间本地配置与会话详情 (已脱敏)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("配置项", style="bold")
    table.add_column("当前值")

    table.add_row("vuex.json 路径", str(cfg.vuex_path or "[yellow]未找到[/yellow]"))
    table.add_row("zspace.conf 路径", str(cfg.conf_path or "[yellow]未找到[/yellow]"))
    table.add_row("CLI 独立会话路径", str(cfg.cli_session_path or "[dim]未创建 (~/.config/zspace-cli/session.json)[/dim]"))
    table.add_row("登录账号", f"{cfg.username} ({cfg.nickname})" if cfg.nickname else cfg.username)
    table.add_row("管理员权限", "是 (Master)" if cfg.is_master else "否")
    table.add_row("NAS 设备名称", f"{cfg.nas_name} ({cfg.device_mode}, {cfg.disk_num} 盘位)")
    table.add_row("NAS ID (脱敏)", mask_secret(cfg.nas_id, 4, 3))
    table.add_row("客户端标识 (Device)", cfg.device)
    table.add_row("客户端指纹 (Device ID)", mask_secret(cfg.device_id, 6, 6))
    table.add_row("会话 Token (脱敏)", f"[green]{mask_secret(cfg.token, 6, 4)}[/green]")
    table.add_row("设备签名 Sign (脱敏)", mask_secret(cfg.sign, 6, 4))
    table.add_row("本地代理网关", cfg.proxy_base_url)
    table.add_row("局域网直连地址", cfg.lan_base_url or "[dim]未探测到[/dim]")
    table.add_row("首选请求地址", f"[bold underline]{cfg.base_url}[/bold underline]")
    table.add_row("Web 协议版本", cfg.web_version)
    table.add_row("本地挂载盘卷名", cfg.mount_volume or "-")

    console.print(table)


@auth_app.command("login")
def login_cmd(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        "-H",
        help="极空间局域网 IP 或网关地址（例如 192.168.1.100 或 http://127.0.0.1:13579）",
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        "-u",
        help="登录手机号或用户名（默认读取当前配置中的账号）",
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        "-p",
        prompt=True,
        hide_input=True,
        help="登录密码（将使用 /key/pub RSA 公钥加密传输）",
    ),
    sms_proof: str = typer.Option("", "--sms-proof", help="新设备二次验证短信凭证 (可选)"),
    no_save: bool = typer.Option(False, "--no-save", help="仅测试登录获取 Token，不写入本地会话文件"),
) -> None:
    """通过局域网 IP + 账号密码（使用 /key/pub + RSA 加密 + /auth/login）独立获取 Token."""
    with ZSpaceClient() as client:
        cfg = client.config
        resolved_user = (username or cfg.username).strip()
        if not resolved_user:
            resolved_user = typer.prompt("请输入极空间账号/手机号")

        target_host = host or cfg.lan_base_url or cfg.base_url
        console.print(
            f"[cyan]正在向 [bold]{target_host}[/bold] 请求 RSA 公钥 (`/key/pub?signme=1`) 并执行加密登录...[/cyan]"
        )

        try:
            res = client.login_with_password(
                username=resolved_user,
                password=password or "",
                host=target_host,
                sms_proof=sms_proof,
                save=not no_save,
            )
        except ZSpaceError as exc:
            console.print(f"[red]! 登录失败:[/red] {exc}")
            hint = ZSpaceError.diagnose(exc.code, exc.msg)
            if hint:
                console.print(f"  [dim]提示: {hint}[/dim]")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]! 登录请求异常:[/red] {exc}")
            raise typer.Exit(1)

        console.print("[bold green]✔ 登录成功！[/bold green]")
        console.print(f"  账号: [bold]{res['username']}[/bold]")
        console.print(f"  设备: [bold]{res['nas_name'] or '-'}[/bold] ({mask_secret(res['nas_id'], 4, 3)})")
        console.print(f"  Token (脱敏): [green]{mask_secret(res['token'], 6, 4)}[/green]")
        if not no_save:
            console.print("  已持久化至: [dim]~/.config/zspace-cli/session.json[/dim]")
