"""极空间 `zs docker` 容器与镜像管理、`zs compose` 编排项目管理命令组.

涵盖 API 模块：
- 模块 94382：极空间原生 ZDocker 服务 (`/zdocker/*`, `/zdocker/v2/*`, `/zdocker/v2/compose/*`)
- 模块 33447：极空间内嵌 Portainer 接口 (`/docker/api/*`，通过 `PortainerClient` 自动鉴权与兼容回退)
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from zspace_cli.clients.portainer import PortainerClient
from zspace_cli.commands.auth_cmd import format_bytes
from zspace_cli.core.client import ZSpaceClient, ZSpaceError

docker_app = typer.Typer(
    name="docker",
    help="Docker 容器生命周期、实时资源监控、日志进程、镜像拉取与 Compose 导出管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

compose_app = typer.Typer(
    name="compose",
    help="Docker Compose 编排项目列表、详情、YAML 查看与启停控制 (`/zdocker/v2/compose/*`)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# 同时支持 `zs docker compose ...` 与顶层 `zs compose ...`
docker_app.add_typer(compose_app, name="compose")

# 导出默认 app 供 main.py 自动挂载
app = docker_app
console = Console()


# ── 辅助函数 ──


def _format_ts(ts: Any) -> str:
    """将 Unix 秒级或毫秒级时间戳格式化为可读时间字符串."""
    try:
        val = float(ts or 0)
        if val <= 0:
            return "-"
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(ts or "-")


def _clean_container_name(container: dict[str, Any]) -> str:
    """提取不带前导 `/` 的容器名称."""
    names = container.get("Names") or []
    if isinstance(names, list) and names:
        return str(names[0]).lstrip("/")
    name = container.get("Name") or container.get("name") or ""
    return str(name).lstrip("/") or str(container.get("Id") or "")[:12]


def _format_ports(ports: Any) -> str:
    """将容器端口列表去重并格式化为紧凑字符串."""
    if not isinstance(ports, list) or not ports:
        return "-"
    seen: list[str] = []
    for p in ports:
        if not isinstance(p, dict):
            continue
        priv = p.get("PrivatePort")
        pub = p.get("PublicPort")
        ptype = p.get("Type") or "tcp"
        entry = f"{pub}->{priv}/{ptype}" if pub else f"{priv}/{ptype}"
        if entry not in seen:
            seen.append(entry)
    return ", ".join(seen) if seen else "-"


def _state_badge(state: str, status_text: str = "") -> str:
    """生成带颜色的容器状态标签."""
    s = (state or "").lower()
    detail = f" ({status_text})" if status_text and status_text.lower() != s else ""
    if s == "running":
        return f"[bold green]running[/bold green]{detail}"
    if s in ("restarting", "paused"):
        return f"[bold yellow]{state}[/bold yellow]{detail}"
    if s in ("exited", "dead", "stopped"):
        return f"[dim red]{state}[/dim red]{detail}"
    return f"{state or '-'}{detail}"


def _resolve_container(client: ZSpaceClient, identifier: str) -> tuple[str, str, dict[str, Any]]:
    """根据容器名称、短 ID 或完整 ID 解析为 `(container_id, container_name, container_dict)`."""
    target = identifier.strip().lstrip("/")
    resp = client.post(
        "/zdocker/v2/container_list",
        json_data={},
        request_as_json=True,
    )
    containers = resp.get("data") or []
    if not isinstance(containers, list):
        containers = []

    # 1. 精确匹配容器名或完整 ID
    for c in containers:
        cid = str(c.get("Id") or "")
        cname = _clean_container_name(c)
        if target == cname or target == cid:
            return cid, cname, c

    # 2. 前缀匹配 ID 或大小写不敏感匹配容器名
    matches: list[tuple[str, str, dict[str, Any]]] = []
    target_lower = target.lower()
    for c in containers:
        cid = str(c.get("Id") or "")
        cname = _clean_container_name(c)
        if cid.lower().startswith(target_lower) or cname.lower() == target_lower:
            matches.append((cid, cname, c))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names_str = ", ".join(f"{m[1]} ({m[0][:12]})" for m in matches)
        raise ZSpaceError(f"容器标识 '{identifier}' 匹配到多个容器: {names_str}，请提供更精确的名称或 ID")

    raise ZSpaceError(f"未找到匹配的容器: '{identifier}'（可通过 `zs docker ps -a` 查看全部容器）")


def _clean_docker_log_line(line: str) -> str:
    """清理极空间 `/zdocker/container_logs` 返回的 Docker stream 8 字节头部控制字符."""
    if not line:
        return ""
    # 前端 44571.cf74c1bf.js 逻辑：时间戳前可能存在 8 字节 Docker stream multiplexed header
    m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line)
    if m and 0 < m.start() <= 12:
        line = line[m.start():]
    # 去除不可见控制字符（保留制表符）
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", line)


def _resolve_compose_project(client: ZSpaceClient, project_ref: str) -> dict[str, Any]:
    """通过项目名称 (`projectName`) 或项目 ID (`id`) 解析 Compose 项目字典."""
    resp = client.post(
        "/zdocker/v2/compose/list_projects",
        json_data={},
        request_as_json=True,
    )
    projects = resp.get("data") or []
    if not isinstance(projects, list):
        projects = []

    target = project_ref.strip()
    for p in projects:
        pid = str(p.get("id") or "")
        pname = str(p.get("projectName") or p.get("name") or "")
        if target in (pid, pname):
            return p

    target_lower = target.lower()
    for p in projects:
        pid = str(p.get("id") or "")
        pname = str(p.get("projectName") or p.get("name") or "")
        if pname.lower() == target_lower or pid.lower().startswith(target_lower):
            return p

    # 若列表为空或未匹配，构造最小字典允许直接按 project_id 尝试调用
    return {"id": target, "projectName": target}


# ── 1. 容器列表与状态监控 (`zs docker ps` / `zs docker stats` / `zs docker info`) ──


@docker_app.command("ps")
def ps_cmd(
    all_containers: bool = typer.Option(True, "--all/--running", "-a", help="显示所有容器（默认包含已停止容器）"),
    search: str = typer.Option("", "--search", "-s", help="按容器名称关键词过滤"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出原始数据"),
) -> None:
    """列出 Docker 容器及实时 CPU/内存占用、端口映射与编排归属 (`/zdocker/v2/container_list`)."""
    with ZSpaceClient() as client:
        payload: dict[str, Any] = {}
        if search:
            payload["search"] = search
        resp = client.post(
            "/zdocker/v2/container_list",
            json_data=payload,
            request_as_json=True,
        )
        containers = resp.get("data") or []
        if not isinstance(containers, list):
            containers = []

        if not all_containers:
            containers = [
                c for c in containers if str(c.get("State") or "").lower() == "running"
            ]

        # 获取实时资源监控数据并建立映射
        stats_map: dict[str, dict[str, Any]] = {}
        try:
            stats_resp = client.get("/zdocker/container_stats", check_code=False)
            for s in stats_resp.get("data") or []:
                if isinstance(s, dict) and s.get("container_id"):
                    stats_map[str(s["container_id"])] = s
        except Exception:
            pass

        if json_output:
            enriched = []
            for c in containers:
                item = dict(c)
                cid = str(c.get("Id") or "")
                if cid in stats_map:
                    item["_stats"] = stats_map[cid]
                enriched.append(item)
            console.print(json.dumps(enriched, ensure_ascii=False, indent=2))
            return

        if not containers:
            console.print("[yellow]当前未发现匹配的 Docker 容器。[/yellow]")
            return

        table = Table(
            title=f"极空间 Docker 容器列表 (共 {len(containers)} 个)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("容器名称 / ID", style="bold white", no_wrap=True)
        table.add_column("镜像 (Image)", style="cyan")
        table.add_column("状态 (Status)", no_wrap=True)
        table.add_column("CPU", justify="right", style="green", no_wrap=True)
        table.add_column("内存占用", justify="right", style="magenta", no_wrap=True)
        table.add_column("端口映射 (Ports)", style="yellow")
        table.add_column("网络 / 归属", style="dim")

        for c in containers:
            cid = str(c.get("Id") or "")
            cname = _clean_container_name(c)
            image = str(c.get("Image") or "-")
            state = str(c.get("State") or "")
            status_text = str(c.get("Status") or "")

            st = stats_map.get(cid) or {}
            cpu_str = str(st.get("cpu_usage") or "-") if state.lower() == "running" else "-"
            mem_used = int(st.get("memory_usage") or 0)
            mem_limit = int(st.get("memory_limit") or 0)
            if state.lower() == "running" and mem_used > 0:
                mem_str = (
                    f"{format_bytes(mem_used)} / {format_bytes(mem_limit)}"
                    if mem_limit > 0
                    else format_bytes(mem_used)
                )
            else:
                mem_str = "-"

            net_mode = str((c.get("HostConfig") or {}).get("NetworkMode") or "-")
            compose_proj = str(c.get("ComposeProject") or "")
            app_name = str(c.get("App") or "")
            extra_parts = [net_mode]
            if compose_proj:
                extra_parts.append(f"compose:{compose_proj}")
            if app_name:
                extra_parts.append(f"app:{app_name}")

            table.add_row(
                f"{cname}\n[dim]{cid[:12]}[/dim]",
                image,
                _state_badge(state, status_text),
                cpu_str,
                mem_str,
                _format_ports(c.get("Ports")),
                " | ".join(extra_parts),
            )

        console.print(table)


@docker_app.command("stats")
def stats_cmd(
    container_name: Optional[str] = typer.Argument(None, help="可选：指定容器名称或 ID 过滤"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看容器实时 CPU、内存、网络速率与磁盘占用 (`/zdocker/container_stats`)."""
    with ZSpaceClient() as client:
        c_resp = client.post(
            "/zdocker/v2/container_list",
            json_data={},
            request_as_json=True,
        )
        containers = c_resp.get("data") or []
        name_map: dict[str, str] = {}
        for c in containers if isinstance(containers, list) else []:
            cid = str(c.get("Id") or "")
            if cid:
                name_map[cid] = _clean_container_name(c)

        s_resp = client.get("/zdocker/container_stats")
        stats_list = s_resp.get("data") or []
        if not isinstance(stats_list, list):
            stats_list = []

        if container_name:
            target = container_name.strip().lstrip("/").lower()
            stats_list = [
                s
                for s in stats_list
                if str(s.get("container_id") or "").lower().startswith(target)
                or name_map.get(str(s.get("container_id") or ""), "").lower() == target
            ]

        if json_output:
            console.print(json.dumps(stats_list, ensure_ascii=False, indent=2))
            return

        if not stats_list:
            console.print("[yellow]当前无运行中的容器监控数据。[/yellow]")
            return

        table = Table(
            title="极空间 Docker 容器实时资源监控 (/zdocker/container_stats)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("容器名称", style="bold white")
        table.add_column("容器短 ID", style="dim")
        table.add_column("CPU (Docker / 系统)", justify="right", style="green")
        table.add_column("内存占用 / 限制", justify="right", style="magenta")
        table.add_column("内存占比", justify="right", style="cyan")
        table.add_column("网络 RX / TX", justify="right", style="yellow")
        table.add_column("容器磁盘占用", justify="right")

        for s in stats_list:
            cid = str(s.get("container_id") or "")
            cname = name_map.get(cid, cid[:12])
            cpu_docker = str(s.get("cpu_usage") or "0.00%")
            cpu_win = str(s.get("cpu_usage_win") or "0.00%")
            mem_used = int(s.get("memory_usage") or 0)
            mem_limit = int(s.get("memory_limit") or 0)
            mem_pct = f"{(mem_used / mem_limit * 100):.1f}%" if mem_limit > 0 else "-"
            rx_rate = format_bytes(int(s.get("rx_rates") or 0)) + "/s"
            tx_rate = format_bytes(int(s.get("tx_rates") or 0)) + "/s"
            disk_info = s.get("disk_usage") or {}
            disk_kb = int(disk_info.get("disk_usage") or 0) if isinstance(disk_info, dict) else 0
            disk_str = format_bytes(disk_kb * 1024) if disk_kb > 0 else "-"

            table.add_row(
                cname,
                cid[:12],
                f"{cpu_docker} ({cpu_win})",
                f"{format_bytes(mem_used)} / {format_bytes(mem_limit)}",
                mem_pct,
                f"↓ {rx_rate} / ↑ {tx_rate}",
                disk_str,
            )

        console.print(table)


# ── 2. 容器启停控制 (`zs docker start` / `stop` / `restart` / `rm`) ──


def _run_container_op(container_ref: str, action: str, action_label: str, json_output: bool) -> None:
    """执行容器操作 (`/zdocker/container_op`)."""
    with ZSpaceClient() as client:
        cid, cname, _ = _resolve_container(client, container_ref)
        resp = client.get(
            "/zdocker/container_op",
            params={"container_id": cid, "action": action},
        )
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(
            f"[bold green]✓[/bold green] 容器 [bold]{cname}[/bold] ([dim]{cid[:12]}[/dim]) 已成功执行 [cyan]{action_label}[/cyan]。"
        )


@docker_app.command("start")
def start_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """启动指定 Docker 容器 (`/zdocker/container_op?action=start`)."""
    _run_container_op(container_name, "start", "启动 (start)", json_output)


@docker_app.command("stop")
def stop_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """停止指定 Docker 容器 (`/zdocker/container_op?action=stop`)."""
    _run_container_op(container_name, "stop", "停止 (stop)", json_output)


@docker_app.command("restart")
def restart_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重启指定 Docker 容器 (`/zdocker/container_op?action=restart`)."""
    _run_container_op(container_name, "restart", "重启 (restart)", json_output)


@docker_app.command("rm")
def rm_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    force: bool = typer.Option(False, "--force", "-f", help="强制删除正在运行的容器 (`force_remove`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """删除指定 Docker 容器 (`/zdocker/container_op?action=remove`)."""
    action = "force_remove" if force else "remove"
    _run_container_op(container_name, action, f"删除 ({action})", json_output)


# ── 3. 容器日志、详情、进程与转 Compose (`logs` / `inspect` / `top` / `to-compose`) ──


@docker_app.command("logs")
def logs_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    tail: int = typer.Option(100, "--tail", "-n", help="显示最后 N 行日志（0 表示显示全部）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看指定 Docker 容器日志 (`/zdocker/container_logs`)."""
    with ZSpaceClient() as client:
        cid, cname, _ = _resolve_container(client, container_name)
        resp = client.get("/zdocker/container_logs", params={"container_id": cid})
        raw_logs = str(resp.get("data") or resp.get("raw_text") or "")
        lines = [_clean_docker_log_line(line) for line in raw_logs.splitlines() if line.strip()]
        if tail > 0:
            lines = lines[-tail:]

        if json_output:
            console.print(
                json.dumps(
                    {"container_id": cid, "name": cname, "lines": lines},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if not lines:
            console.print(f"[yellow]容器 {cname} ({cid[:12]}) 当前暂无日志输出。[/yellow]")
            return

        console.print(
            Panel(
                "\n".join(lines),
                title=f"容器日志: [bold cyan]{cname}[/bold cyan] ([dim]{cid[:12]}[/dim]) - 最近 {len(lines)} 行",
                border_style="cyan",
            )
        )


@docker_app.command("inspect")
def inspect_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出完整 Docker Inspect 数据"),
) -> None:
    """查看容器详细配置、挂载卷、环境变量与网络信息 (`/zdocker/container_info`)."""
    with ZSpaceClient() as client:
        cid, cname, _ = _resolve_container(client, container_name)
        resp = client.get("/zdocker/container_info", params={"container_id": cid})
        info = resp.get("data") or {}

        if json_output:
            console.print(json.dumps(info, ensure_ascii=False, indent=2))
            return

        state = info.get("State") or {}
        config = info.get("Config") or {}
        host_cfg = info.get("HostConfig") or {}
        net_settings = info.get("NetworkSettings") or {}
        mounts = info.get("Mounts") or []

        restart_policy = (host_cfg.get("RestartPolicy") or {}).get("Name") or "-"
        net_mode = host_cfg.get("NetworkMode") or "-"
        networks = net_settings.get("Networks") or {}
        ip_list = [
            f"{net_name}: {net_val.get('IPAddress') or '-'}"
            for net_name, net_val in networks.items()
            if isinstance(net_val, dict)
        ]

        summary_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        summary_table.add_column("Key", style="bold cyan", no_wrap=True)
        summary_table.add_column("Value")
        summary_table.add_row("容器名称", cname)
        summary_table.add_row("容器 ID", cid)
        summary_table.add_row("镜像 (Image)", str(config.get("Image") or info.get("Image") or "-"))
        summary_table.add_row(
            "运行状态",
            f"{state.get('Status', '-')} (PID: {state.get('Pid', '-')}, ExitCode: {state.get('ExitCode', 0)})",
        )
        summary_table.add_row("启动时间", str(state.get("StartedAt") or "-"))
        summary_table.add_row("入口命令", " ".join(str(x) for x in [info.get("Path") or ""] + (info.get("Args") or [])))
        summary_table.add_row("重启策略", str(restart_policy))
        summary_table.add_row("网络模式 / IP", f"{net_mode} ({', '.join(ip_list) if ip_list else '-'})")

        if mounts:
            mount_lines = [
                f"[cyan]{m.get('Source')}[/cyan] -> [green]{m.get('Destination')}[/green] ({'rw' if m.get('RW') else 'ro'})"
                for m in mounts
                if isinstance(m, dict)
            ]
            summary_table.add_row("目录挂载 (Mounts)", "\n".join(mount_lines))

        env_list = config.get("Env") or []
        if env_list:
            summary_table.add_row("环境变量 (Env)", "\n".join(str(e) for e in env_list[:20]))

        console.print(
            Panel(
                summary_table,
                title=f"Docker 容器详情: [bold]{cname}[/bold]",
                border_style="cyan",
            )
        )


@docker_app.command("top")
def top_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看容器内部正在运行的进程列表 (`/zdocker/container_top`)."""
    with ZSpaceClient() as client:
        cid, cname, _ = _resolve_container(client, container_name)
        resp = client.get("/zdocker/container_top", params={"container_id": cid})
        data = resp.get("data") or {}

        if json_output:
            console.print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        titles = data.get("Titles") or ["UID", "PID", "PPID", "C", "STIME", "TTY", "TIME", "CMD"]
        processes = data.get("Processes") or []

        if not processes:
            console.print(f"[yellow]容器 {cname} 当前未返回进程列表（请确认容器处于运行状态）。[/yellow]")
            return

        table = Table(
            title=f"容器内部进程列表: {cname} ({cid[:12]})",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        for col in titles:
            table.add_column(str(col), style="green" if col == "PID" else None)

        for proc in processes:
            if isinstance(proc, list):
                table.add_row(*[str(cell) for cell in proc])

        console.print(table)


@docker_app.command("to-compose")
def to_compose_cmd(
    container_name: str = typer.Argument(..., help="容器名称或容器 ID"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="可选：将生成的 docker-compose.yml 保存到文件"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """将现有容器配置逆向导出为 `docker-compose.yml` (`/zdocker/container_convert_compose`)."""
    with ZSpaceClient() as client:
        cid, cname, _ = _resolve_container(client, container_name)
        resp = client.post(
            "/zdocker/container_convert_compose",
            json_data={"container_id": cid},
            request_as_json=True,
            check_code=False,
        )
        yaml_text = str(resp.get("raw_text") or resp.get("data") or "").strip()
        if not yaml_text:
            raise ZSpaceError(f"未能导出容器 {cname} 的 Compose 配置", data=resp)

        if output:
            output.write_text(yaml_text + "\n", encoding="utf-8")
            console.print(f"[bold green]✓[/bold green] 已导出 Compose 配置至: [cyan]{output}[/cyan]")

        if json_output:
            console.print(
                json.dumps(
                    {"container_id": cid, "name": cname, "compose_yaml": yaml_text},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        console.print(
            Panel(
                Syntax(yaml_text, "yaml", theme="monokai", line_numbers=True),
                title=f"容器转 Compose YAML: [bold cyan]{cname}[/bold cyan] ([dim]{cid[:12]}[/dim])",
                border_style="cyan",
            )
        )


# ── 4. 镜像管理 (`images` / `search` / `tags` / `pull` / `mirrors` / `info`) ──


@docker_app.command("images")
def images_cmd(
    common: bool = typer.Option(False, "--common", help="查看极空间官方精选常用镜像列表 (`/zdocker/common_image_list`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出本地 Docker 镜像或极空间精选常用镜像 (`/zdocker/v2/local_image_list`)."""
    with ZSpaceClient() as client:
        if common:
            resp = client.get("/zdocker/common_image_list")
            images = (resp.get("data") or {}).get("images") or []
            if json_output:
                console.print(json.dumps(images, ensure_ascii=False, indent=2))
                return
            table = Table(
                title=f"极空间精选常用镜像 (共 {len(images)} 个)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("镜像名称", style="bold white")
            table.add_column("离线包标识 (file)", style="cyan")
            table.add_column("大小", justify="right", style="magenta")
            table.add_column("Star", justify="right", style="yellow")
            table.add_column("简介")
            for img in images:
                table.add_row(
                    str(img.get("name") or "-"),
                    str(img.get("file") or "-"),
                    format_bytes(int(img.get("size") or 0)),
                    str(img.get("star") or "0"),
                    str(img.get("desc") or "-"),
                )
            console.print(table)
            return

        resp = client.post(
            "/zdocker/v2/local_image_list",
            json_data={},
            request_as_json=True,
        )
        images = resp.get("data") or []
        if not isinstance(images, list):
            images = []

        if json_output:
            console.print(json.dumps(images, ensure_ascii=False, indent=2))
            return

        if not images:
            console.print("[yellow]当前本地无 Docker 镜像。[/yellow]")
            return

        table = Table(
            title=f"极空间本地 Docker 镜像列表 (共 {len(images)} 个)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("仓库标签 (RepoTags)", style="bold white")
        table.add_column("镜像 ID", style="dim", no_wrap=True)
        table.add_column("大小 (Size)", justify="right", style="magenta")
        table.add_column("创建时间", style="cyan")
        table.add_column("关联容器 / 可更新", style="green")

        for img in images:
            repo_tags = img.get("RepoTags") or []
            tag_str = ", ".join(repo_tags) if repo_tags else "<none>:<none>"
            img_id = str(img.get("Id") or "").replace("sha256:", "")[:12]
            size_str = format_bytes(int(img.get("Size") or 0))
            created_str = _format_ts(img.get("Created"))
            upd_info = img.get("Update") or {}
            can_upd = bool(upd_info.get("Update"))
            used_containers: list[str] = []
            for cname_list in (upd_info.get("Containers") or {}).values():
                if isinstance(cname_list, list):
                    used_containers.extend(str(x) for x in cname_list)
            extra = ", ".join(used_containers) if used_containers else "未使用"
            if can_upd:
                extra += " [bold yellow](有更新)[/bold yellow]"

            table.add_row(tag_str, img_id, size_str, created_str, extra)

        console.print(table)


@docker_app.command("search")
def search_cmd(
    keyword: str = typer.Argument(..., help="要搜索的镜像关键词（如 nginx, jellyfin, alist）"),
    page: int = typer.Option(1, "--page", "-p", help="分页页码"),
    common: bool = typer.Option(False, "--common", help="在极空间常用镜像库中搜索 (`/zdocker/common_image_search`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """在 Docker Hub 或极空间镜像库中搜索镜像 (`/zdocker/hub_image_search`)."""
    with ZSpaceClient() as client:
        if common:
            resp = client.get("/zdocker/common_image_search", params={"image_name": keyword})
            results = (resp.get("data") or {}).get("images") or []
            if json_output:
                console.print(json.dumps(results, ensure_ascii=False, indent=2))
                return
            if not results:
                console.print(f"[yellow]未在常用镜像库中找到 '{keyword}'。[/yellow]")
                return
            table = Table(title=f"常用镜像搜索结果: {keyword}", box=box.ROUNDED, header_style="bold cyan")
            table.add_column("名称", style="bold white")
            table.add_column("文件标识 (用于 pull --common)", style="cyan")
            table.add_column("大小", justify="right", style="magenta")
            table.add_column("描述")
            for r in results:
                table.add_row(
                    str(r.get("name") or "-"),
                    str(r.get("file") or "-"),
                    format_bytes(int(r.get("size") or 0)),
                    str(r.get("desc") or "-"),
                )
            console.print(table)
            return

        resp = client.get("/zdocker/hub_image_search", params={"term": keyword, "page": page})
        data = resp.get("data") or {}
        results = data.get("results") or []

        if json_output:
            console.print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        if not results:
            console.print(f"[yellow]未在 Docker Hub 找到与 '{keyword}' 匹配的镜像。[/yellow]")
            return

        table = Table(
            title=f"Docker Hub 镜像搜索: '{keyword}' (第 {page} 页，共 {len(results)} 条)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("镜像仓库 (Repo)", style="bold white")
        table.add_column("Stars", justify="right", style="yellow")
        table.add_column("拉取次数 (Pulls)", justify="right", style="green")
        table.add_column("官方", justify="center", style="cyan")
        table.add_column("简介 (Description)")

        for r in results:
            table.add_row(
                str(r.get("repo_name") or "-"),
                str(r.get("star_count") or 0),
                f"{int(r.get('pull_count') or 0):,}",
                "[bold green]✓[/bold green]" if r.get("is_official") else "-",
                str(r.get("short_description") or "-"),
            )

        console.print(table)


@docker_app.command("tags")
def tags_cmd(
    repo_name: str = typer.Argument(..., help="镜像仓库名称（如 nginx 或 linuxserver/jellyfin）"),
    official: Optional[bool] = typer.Option(None, "--official/--no-official", help="是否为官方镜像（默认自动根据是否含 `/` 判断）"),
    limit: int = typer.Option(25, "--limit", "-n", help="显示前 N 个版本标签"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查询指定 Docker Hub 镜像的可用版本标签 (`/zdocker/hub_image_tags`)."""
    is_official = official if official is not None else ("/" not in repo_name)
    with ZSpaceClient() as client:
        resp = client.get(
            "/zdocker/hub_image_tags",
            params={"repo_name": repo_name, "is_official": str(is_official).lower()},
        )
        tags = resp.get("data") or []
        if not isinstance(tags, list):
            tags = []

        if json_output:
            console.print(json.dumps(tags[:limit] if limit > 0 else tags, ensure_ascii=False, indent=2))
            return

        if not tags:
            console.print(f"[yellow]未查询到镜像 '{repo_name}' 的标签列表。[/yellow]")
            return

        shown = tags[:limit] if limit > 0 else tags
        table = Table(
            title=f"镜像可用标签: {repo_name} (显示 {len(shown)}/{len(tags)} 个)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("标签名 (Tag)", style="bold white")
        table.add_column("压缩大小", justify="right", style="magenta")
        table.add_column("状态", style="green")
        table.add_column("最后更新时间", style="cyan")

        for t in shown:
            table.add_row(
                str(t.get("name") or "-"),
                format_bytes(int(t.get("full_size") or 0)),
                str(t.get("tag_status") or "-"),
                str(t.get("last_updated") or "-"),
            )

        console.print(table)


@docker_app.command("pull")
def pull_cmd(
    image_ref: str = typer.Argument(..., help="镜像名称及标签（如 `nginx:latest`）或常用镜像文件标识"),
    common: bool = typer.Option(False, "--common", help="使用极空间常用镜像加速下载通道 (`/zdocker/common_image_download`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """触发异步拉取 Docker 镜像任务 (`/zdocker/image_pull` 或 `/zdocker/common_image_download`)."""
    with ZSpaceClient() as client:
        if common:
            resp = client.get(
                "/zdocker/common_image_download",
                params={"image_file_name": image_ref},
            )
        else:
            normalized = image_ref if ":" in image_ref or "@" in image_ref else f"{image_ref}:latest"
            resp = client.get(
                "/zdocker/image_pull",
                params={"image_name": normalized},
            )

        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return

        task_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
        console.print(
            f"[bold green]✓[/bold green] 已提交镜像拉取任务: [bold cyan]{image_ref}[/bold cyan]"
            + (f" (任务 ID: [yellow]{task_id}[/yellow])" if task_id else "")
        )


@docker_app.command("mirrors")
def mirrors_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看极空间 Docker 镜像源与加速器配置 (`/zdocker/v2/registry_mirrors`)."""
    with ZSpaceClient() as client:
        resp = client.get("/zdocker/v2/registry_mirrors")
        mirrors = resp.get("data") or []

        if json_output:
            console.print(json.dumps(mirrors, ensure_ascii=False, indent=2))
            return

        table = Table(
            title="极空间 Docker 镜像加速器列表 (/zdocker/v2/registry_mirrors)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("序号", justify="right", style="dim")
        table.add_column("名称 (Name)", style="bold white")
        table.add_column("地址 / 说明 (URL)", style="cyan")

        for idx, m in enumerate(mirrors if isinstance(mirrors, list) else [], start=1):
            table.add_row(str(idx), str(m.get("name") or "-"), str(m.get("url") or "-"))

        console.print(table)


@docker_app.command("info")
def docker_info_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看极空间 Docker 引擎版本、Compose 版本与运行状态 (`/zdocker/docker_info`)."""
    with PortainerClient() as pt_client:
        resp = pt_client.docker_info()
        data = resp.get("data") if "data" in resp else resp

        if json_output:
            console.print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        if isinstance(data, dict):
            for k, v in data.items():
                table.add_row(str(k), str(v))
        console.print(Panel(table, title="极空间 Docker 引擎信息", border_style="cyan"))


# ── 5. Docker Compose 编排管理 (`zs compose ls/info/logs/up/down/restart/rebuild`) ──


@compose_app.command("ls")
def compose_ls_cmd(
    search: str = typer.Option("", "--search", "-s", help="按 Compose 项目名关键词过滤"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出所有 Docker Compose 编排项目及运行状态 (`/zdocker/v2/compose/list_projects`)."""
    with ZSpaceClient() as client:
        payload: dict[str, Any] = {}
        if search:
            payload["search"] = search
        resp = client.post(
            "/zdocker/v2/compose/list_projects",
            json_data=payload,
            request_as_json=True,
        )
        projects = resp.get("data") or []
        if not isinstance(projects, list):
            projects = []

        if json_output:
            console.print(json.dumps(projects, ensure_ascii=False, indent=2))
            return

        if not projects:
            console.print("[yellow]当前未发现 Docker Compose 编排项目。[/yellow]")
            return

        table = Table(
            title=f"极空间 Docker Compose 项目列表 (共 {len(projects)} 个)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("项目名称 (Project)", style="bold white")
        table.add_column("项目 ID", style="dim")
        table.add_column("容器状态 (Status)", style="green")
        table.add_column("创建时间", style="cyan")
        table.add_column("配置路径 / 来源", style="yellow")

        for p in projects:
            pname = str(p.get("projectName") or p.get("name") or "-")
            pid = str(p.get("id") or "-")
            status = str(p.get("status") or "-")
            created = _format_ts(p.get("createdAt") or p.get("created"))
            cfg_path = str(p.get("configFiles") or p.get("path") or p.get("App") or "-")
            table.add_row(pname, pid, status, created, cfg_path)

        console.print(table)


@compose_app.command("info")
def compose_info_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看 Compose 项目的容器列表与 `docker-compose.yml` 文件内容."""
    with ZSpaceClient() as client:
        proj = _resolve_compose_project(client, project)
        pid = str(proj.get("id") or project)
        pname = str(proj.get("projectName") or project)

        c_resp = client.get(
            "/zdocker/v2/compose/list_project_containers",
            params={"project_id": pid},
            check_code=False,
        )
        containers = c_resp.get("data") if isinstance(c_resp.get("data"), list) else []

        f_resp = client.get(
            "/zdocker/v2/compose/get_project_file",
            params={"project_id": pid},
            check_code=False,
        )
        file_data = f_resp.get("data") if isinstance(f_resp.get("data"), dict) else {}
        yaml_content = str(file_data.get("file_content") or "").strip()

        if json_output:
            console.print(
                json.dumps(
                    {
                        "project": proj,
                        "containers": containers,
                        "file": file_data,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        console.print(
            Panel(
                f"项目名称: [bold cyan]{pname}[/bold cyan]  |  项目 ID: [yellow]{pid}[/yellow]  |  状态: {proj.get('status', '-')}",
                title="Compose 项目概览",
                border_style="cyan",
            )
        )

        if containers:
            table = Table(
                title=f"项目容器列表 ({len(containers)} 个)",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("容器名称", style="bold white")
            table.add_column("镜像", style="cyan")
            table.add_column("状态")
            table.add_column("端口映射", style="yellow")
            for c in containers:
                table.add_row(
                    _clean_container_name(c),
                    str(c.get("Image") or "-"),
                    _state_badge(str(c.get("State") or ""), str(c.get("Status") or "")),
                    _format_ports(c.get("Ports")),
                )
            console.print(table)

        if yaml_content:
            console.print(
                Panel(
                    Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True),
                    title=f"docker-compose.yml ({pname})",
                    border_style="cyan",
                )
            )


@compose_app.command("logs")
def compose_logs_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    tail: int = typer.Option(100, "--tail", "-n", help="显示最后 N 行日志"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看 Compose 项目编排日志 (`/zdocker/v2/compose/logs_project`)."""
    with ZSpaceClient() as client:
        proj = _resolve_compose_project(client, project)
        pid = str(proj.get("id") or project)
        pname = str(proj.get("projectName") or project)
        resp = client.get("/zdocker/v2/compose/logs_project", params={"project_id": pid})
        raw_logs = str(resp.get("data") or "")
        lines = [line for line in raw_logs.splitlines() if line.strip()]
        if tail > 0:
            lines = lines[-tail:]

        if json_output:
            console.print(json.dumps({"project_id": pid, "name": pname, "lines": lines}, ensure_ascii=False, indent=2))
            return

        console.print(
            Panel(
                "\n".join(lines) if lines else "[dim]暂无日志输出[/dim]",
                title=f"Compose 项目日志: [bold cyan]{pname}[/bold cyan] ({pid})",
                border_style="cyan",
            )
        )


def _run_compose_op(project: str, endpoint: str, label: str, extra_params: Optional[dict[str, Any]], json_output: bool) -> None:
    """执行 Compose 项目启停操作."""
    with ZSpaceClient() as client:
        proj = _resolve_compose_project(client, project)
        pid = str(proj.get("id") or project)
        pname = str(proj.get("projectName") or project)
        params: dict[str, Any] = {"project_id": pid}
        if extra_params:
            params.update(extra_params)
        resp = client.get(endpoint, params=params)
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(
            f"[bold green]✓[/bold green] Compose 项目 [bold]{pname}[/bold] ([dim]{pid}[/dim]) 已成功执行 [cyan]{label}[/cyan]。"
        )


@compose_app.command("up")
def compose_up_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """启动指定 Docker Compose 项目 (`/zdocker/compose_start_project`)."""
    _run_compose_op(project, "/zdocker/compose_start_project", "启动 (up)", None, json_output)


@compose_app.command("down")
def compose_down_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    delete: bool = typer.Option(False, "--delete", help="彻底删除该 Compose 项目 (`/zdocker/v2/compose/delete_project`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """停止（或删除）指定 Docker Compose 项目 (`/zdocker/compose_stop_project`)."""
    if delete:
        _run_compose_op(project, "/zdocker/v2/compose/delete_project", "删除 (delete)", None, json_output)
    else:
        _run_compose_op(project, "/zdocker/compose_stop_project", "停止 (down)", None, json_output)


@compose_app.command("restart")
def compose_restart_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重启指定 Docker Compose 项目 (`/zdocker/compose_restart_project`)."""
    _run_compose_op(project, "/zdocker/compose_restart_project", "重启 (restart)", None, json_output)


@compose_app.command("rebuild")
def compose_rebuild_cmd(
    project: str = typer.Argument(..., help="Compose 项目名称或项目 ID"),
    pull: bool = typer.Option(False, "--pull", help="重建前拉取最新镜像"),
    start: bool = typer.Option(True, "--start/--no-start", help="重建后立即启动"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重新构建指定 Docker Compose 项目 (`/zdocker/v2/compose/rebuild_project`)."""
    _run_compose_op(
        project,
        "/zdocker/v2/compose/rebuild_project",
        "重建 (rebuild)",
        {"pull": str(pull).lower(), "start": str(start).lower()},
        json_output,
    )
