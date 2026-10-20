"""极空间 `zs vm` KVM 虚拟机管理与快照控制命令组.

涵盖 API 模块：
- 模块 80149：极空间 KVM 虚拟机管理服务 (`/v1/vm/domain/*`，后端守护进程为 `zsvirt` / `qemu-system-x86_64`)
  - 列表与宿主机状态：`POST /v1/vm/domain/domainList`, `POST /v1/vm/domain/getPhyHostStatus`, `POST /v1/vm/domain/checkEnv`
  - 详情查询：`POST /v1/vm/domain/domainBaseInfo`, `POST /v1/vm/domain/domainDetail`
  - 电源控制：`POST /v1/vm/domain/domainSetStatus` (`status`: `start` / `shutdown` / `destroy` / `reboot` / `suspend` / `resume`)
  - 快照管理：`POST /v1/vm/domain/snapshotList`, `snapshotCreate`, `snapshotRevert`, `snapshotDelete`, `getsnapshotstate`
- 模块 81792：极空间应用中心 (`/AppStore/v1/list`)，用于检测 `virtualMachine` (`zsvirt`) 组件安装与运行状态。
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands.auth_cmd import format_bytes
from zspace_cli.core.client import ZSpaceClient, ZSpaceError

vm_app = typer.Typer(
    name="vm",
    help="极空间 KVM 虚拟机 (zsvirt) 列表、详情、电源状态控制与快照管理 (`/v1/vm/domain/*`)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

snapshot_app = typer.Typer(
    name="snapshot",
    help="KVM 虚拟机快照 (Snapshot) 列表、创建、恢复与删除管理",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

vm_app.add_typer(snapshot_app, name="snapshot")

# 导出默认 app 供 main.py 自动挂载
app = vm_app
console = Console()


# ── 状态映射与辅助函数 ──

VM_STATE_LABELS: dict[int, tuple[str, str]] = {
    1: ("运行中 (Running)", "bold green"),
    3: ("已暂停 (Paused)", "bold yellow"),
    5: ("已关机 (Shutoff)", "dim"),
    21: ("异常挂起", "bold red"),
    30: ("处理中", "cyan"),
    101: ("导出中 (Exporting)", "cyan"),
    102: ("迁移中 (Migrating)", "cyan"),
    103: ("克隆中 (Cloning)", "cyan"),
    104: ("导入中 (Importing)", "cyan"),
    105: ("删除快照中", "cyan"),
    106: ("创建快照中", "cyan"),
    107: ("恢复快照中", "cyan"),
}


def _format_vm_state(state_val: Any, state_lang: str = "") -> str:
    """格式化 KVM 虚拟机状态码."""
    try:
        code = int(state_val)
    except (TypeError, ValueError):
        return state_lang or str(state_val or "-")

    label, style = VM_STATE_LABELS.get(code, (state_lang or f"状态({code})", "white"))
    if state_lang and state_lang not in label:
        label = f"{label} [{state_lang}]"
    return f"[{style}]{label}[/{style}]"


def _check_vm_app_status(client: ZSpaceClient) -> dict[str, Any]:
    """通过 `/AppStore/v1/list` 查询 `virtualMachine` (`zsvirt`) 应用包安装与运行状态."""
    try:
        resp = client.post("/AppStore/v1/list", check_code=False)
        app_list = (resp.get("data") or {}).get("list") or []
        for item in app_list:
            if isinstance(item, dict) and item.get("app_name") == "virtualMachine":
                latest_ver = ((item.get("latest_version") or {}).get("version") or {}).get("version") or "-"
                local_ver = ((item.get("local_info") or {}).get("version") or {}).get("version") or "-"
                return {
                    "app_name": "virtualMachine",
                    "installed": bool(item.get("installed")),
                    "status": str(item.get("status") or "unknown"),
                    "local_version": local_ver,
                    "latest_version": latest_ver,
                }
    except Exception:
        pass
    return {"app_name": "virtualMachine", "installed": False, "status": "unknown"}


def _request_vm_api(
    client: ZSpaceClient,
    endpoint: str,
    *,
    data: Optional[dict[str, Any]] = None,
    method: str = "POST",
    json_output: bool = False,
    quiet: bool = False,
) -> Optional[dict[str, Any]]:
    """调用 `/v1/vm/domain/*` 接口，若 `zsvirt` 未安装/未启动导致 HTTP 502 则优雅提示."""
    try:
        return client.request(method, endpoint, data=data)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 502:
            if quiet:
                return None
            app_status = _check_vm_app_status(client)
            msg = (
                "极空间 KVM 虚拟机服务 (zsvirt) 当前未安装"
                if not app_status.get("installed")
                else f"极空间 KVM 虚拟机服务 (zsvirt) 当前未运行 (状态: {app_status.get('status')})"
            )
            if json_output:
                console.print(
                    json.dumps(
                        {
                            "ok": False,
                            "http_status": 502,
                            "error": msg,
                            "vm_service": app_status,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return None
            console.print(
                Panel(
                    f"[bold yellow]{msg}[/bold yellow]\n"
                    f"• 后端网关返回: [dim]HTTP 502 Bad Gateway ({endpoint})[/dim]\n"
                    f"• 应用中心组件: [cyan]virtualMachine[/cyan] (已安装: [bold]{'是' if app_status.get('installed') else '否'}[/bold], "
                    f"最新版本: {app_status.get('latest_version', '-')})\n"
                    "• 提示: 请先在极空间「应用中心」安装并启动「虚拟机」应用后再使用 `zs vm` 命令。",
                    title="虚拟机服务未就绪",
                    border_style="yellow",
                )
            )
            return None
        raise


# ── 1. 虚拟机列表与详情 (`zs vm ls` / `zs vm info` / `zs vm host`) ──


@vm_app.command("ls")
def vm_ls_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出所有 KVM 虚拟机及运行状态 (`/v1/vm/domain/domainList`)."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(client, "/v1/vm/domain/domainList", json_output=json_output)
        if resp is None:
            return

        vm_list = resp.get("data") or []
        if not isinstance(vm_list, list):
            vm_list = []

        if json_output:
            console.print(json.dumps(vm_list, ensure_ascii=False, indent=2))
            return

        if not vm_list:
            console.print("[yellow]当前未创建任何 KVM 虚拟机。[/yellow]")
            return

        table = Table(
            title=f"极空间 KVM 虚拟机列表 (共 {len(vm_list)} 台)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("虚拟机名称 (Name)", style="bold white")
        table.add_column("状态 (State)")
        table.add_column("CPU 核数 / 占用", justify="right", style="green")
        table.add_column("内存分配", justify="right", style="magenta")
        table.add_column("VNC 端口", justify="center", style="yellow")
        table.add_column("开机自启", justify="center", style="cyan")
        table.add_column("备注 / 系统类型", style="dim")

        for vm in vm_list:
            name = str(vm.get("name") or "-")
            state_str = _format_vm_state(vm.get("state"), str(vm.get("stateLang") or ""))
            cpus = vm.get("cpus") or vm.get("vcpu") or "-"
            cpu_pct = vm.get("cpuPercent")
            cpu_col = f"{cpus} 核 ({cpu_pct}%)" if cpu_pct is not None else f"{cpus} 核"

            mem_val = vm.get("memory") or vm.get("maxMem") or 0
            try:
                mem_num = int(mem_val)
                # libvirt/zsvirt 内存可能以 MB、GB 或 Bytes 返回
                if 0 < mem_num <= 512:
                    mem_str = f"{mem_num} GB"
                elif 512 < mem_num <= 1048576:
                    mem_str = format_bytes(mem_num * 1024 * 1024)
                else:
                    mem_str = format_bytes(mem_num)
            except (TypeError, ValueError):
                mem_str = str(mem_val or "-")

            vnc_list = vm.get("vnc") or []
            vnc_port = str(vm.get("vncPort") or (vnc_list[0] if vnc_list else "-"))
            autostart = "✓" if vm.get("autostart") in (1, True, "yes", "true") else "-"
            remark = str(vm.get("remark") or vm.get("osType") or "-")

            table.add_row(name, state_str, cpu_col, mem_str, vnc_port, autostart, remark)

        console.print(table)


@vm_app.command("info")
def vm_info_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """查看指定虚拟机的基础配置、虚拟磁盘与网卡详情 (`domainBaseInfo` & `domainDetail`)."""
    with ZSpaceClient() as client:
        detail_resp = _request_vm_api(
            client,
            "/v1/vm/domain/domainDetail",
            data={"name": vm_name},
            json_output=json_output,
        )
        if detail_resp is None:
            return

        base_resp = _request_vm_api(
            client,
            "/v1/vm/domain/domainBaseInfo",
            data={"name": vm_name},
            json_output=False,
        )
        detail_data = detail_resp.get("data") or {}
        base_data = (base_resp or {}).get("data") or {}

        if json_output:
            console.print(
                json.dumps(
                    {"base_info": base_data, "detail_info": detail_data},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        merged = {**base_data, **detail_data}
        summary = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        summary.add_column("Key", style="bold cyan", no_wrap=True)
        summary.add_column("Value")

        summary.add_row("虚拟机名称", str(merged.get("name") or vm_name))
        summary.add_row("运行状态", _format_vm_state(merged.get("state"), str(merged.get("stateLang") or "")))
        summary.add_row("CPU 核数", str(merged.get("cpus") or merged.get("vcpu") or "-"))
        summary.add_row("内存大小", str(merged.get("memory") or "-"))
        summary.add_row("引导方式 (Boot)", str(merged.get("boot") or merged.get("loader") or "-"))
        vnc_arr = merged.get("vnc") or []
        summary.add_row("VNC 地址", ", ".join(str(x) for x in vnc_arr) if vnc_arr else str(merged.get("vncPort") or "-"))

        sda_list = merged.get("sdaList") or []
        if sda_list:
            disk_lines = [
                f"[cyan]{d.get('target') or d.get('diskname') or '-'}[/cyan] ({d.get('bus') or 'virtio'}): "
                f"[magenta]{d.get('capacity') or '-'}[/magenta] -> {d.get('locate') or d.get('path') or '-'}"
                for d in sda_list
                if isinstance(d, dict)
            ]
            summary.add_row("虚拟磁盘 (sdaList)", "\n".join(disk_lines))

        iface_list = merged.get("interfaceList") or []
        if iface_list:
            net_lines = [
                f"[green]{n.get('type') or n.get('mode') or 'bridge'}[/green] "
                f"(MAC: [yellow]{n.get('macAddr') or n.get('macaddr') or '-'}[/yellow], "
                f"驱动: {n.get('driver') or 'virtio'}, IP: {n.get('ip') or '-'})"
                for n in iface_list
                if isinstance(n, dict)
            ]
            summary.add_row("虚拟网卡 (NICs)", "\n".join(net_lines))

        console.print(Panel(summary, title=f"KVM 虚拟机详情: [bold]{vm_name}[/bold]", border_style="cyan"))


@vm_app.command("host")
def vm_host_cmd(
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """检查宿主机 KVM 虚拟化环境与 `zsvirt` 服务状态 (`getPhyHostStatus` / `checkEnv`)."""
    with ZSpaceClient() as client:
        app_status = _check_vm_app_status(client)
        env_check = client.post("/env/checkVmService", check_code=False)
        phy_resp = _request_vm_api(client, "/v1/vm/domain/getPhyHostStatus", quiet=json_output)

        payload = {
            "app_store_vm": app_status,
            "env_check_vm_service": env_check.get("data"),
            "phy_host_status": (phy_resp or {}).get("data") if phy_resp else None,
        }
        if json_output:
            console.print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if phy_resp and phy_resp.get("data"):
            table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
            table.add_column("Key", style="bold cyan")
            table.add_column("Value")
            for k, v in (phy_resp.get("data") or {}).items():
                table.add_row(str(k), str(v))
            console.print(Panel(table, title="KVM 宿主机状态 (/v1/vm/domain/getPhyHostStatus)", border_style="cyan"))


# ── 2. 虚拟机电源状态控制 (`start` / `stop` / `reboot` / `pause` / `resume`) ──


def _run_vm_power_action(vm_name: str, status_action: str, label: str, json_output: bool) -> None:
    """调用 `POST /v1/vm/domain/domainSetStatus` 控制虚拟机电源状态."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(
            client,
            "/v1/vm/domain/domainSetStatus",
            data={"name": vm_name, "status": status_action},
            json_output=json_output,
        )
        if resp is None:
            return
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(
            f"[bold green]✓[/bold green] 虚拟机 [bold]{vm_name}[/bold] 已成功执行电源操作: [cyan]{label}[/cyan]。"
        )


@vm_app.command("start")
def vm_start_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """启动指定 KVM 虚拟机 (`domainSetStatus: start`)."""
    _run_vm_power_action(vm_name, "start", "启动 (start)", json_output)


@vm_app.command("stop")
def vm_stop_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    force: bool = typer.Option(False, "--force", "-f", help="强制断电关机 (`destroy`)，默认发送 ACPI 关机 (`shutdown`)"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """关闭指定 KVM 虚拟机 (`domainSetStatus: shutdown / destroy`)."""
    status_code = "destroy" if force else "shutdown"
    label = "强制断电 (destroy)" if force else "正常关机 (shutdown)"
    _run_vm_power_action(vm_name, status_code, label, json_output)


@vm_app.command("reboot")
def vm_reboot_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """重启指定 KVM 虚拟机 (`domainSetStatus: reboot`)."""
    _run_vm_power_action(vm_name, "reboot", "重启 (reboot)", json_output)


@vm_app.command("pause")
def vm_pause_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """暂停/挂起指定 KVM 虚拟机 (`domainSetStatus: suspend`)."""
    _run_vm_power_action(vm_name, "suspend", "暂停 (suspend)", json_output)


@vm_app.command("resume")
def vm_resume_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """恢复已暂停的 KVM 虚拟机 (`domainSetStatus: resume`)."""
    _run_vm_power_action(vm_name, "resume", "恢复运行 (resume)", json_output)


# ── 3. 虚拟机快照管理 (`zs vm snapshot ls/create/restore/rm`) ──


@snapshot_app.command("ls")
def vm_snapshot_ls_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """列出指定 KVM 虚拟机的所有快照 (`/v1/vm/domain/snapshotList`)."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(
            client,
            "/v1/vm/domain/snapshotList",
            data={"name": vm_name},
            json_output=json_output,
        )
        if resp is None:
            return

        snaps = resp.get("data") or []
        if not isinstance(snaps, list):
            snaps = []

        if json_output:
            console.print(json.dumps(snaps, ensure_ascii=False, indent=2))
            return

        if not snaps:
            console.print(f"[yellow]虚拟机 '{vm_name}' 当前没有任何快照。[/yellow]")
            return

        table = Table(
            title=f"虚拟机快照列表: {vm_name} (共 {len(snaps)} 个)",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("快照名称 (snapshotName)", style="bold white")
        table.add_column("创建时间", style="cyan")
        table.add_column("快照时状态", style="green")
        table.add_column("备注 (Remark)", style="yellow")

        for s in snaps:
            table.add_row(
                str(s.get("name") or s.get("snapshotName") or "-"),
                str(s.get("createTime") or s.get("time") or "-"),
                str(s.get("state") or s.get("statusLang") or "-"),
                str(s.get("remark") or "-"),
            )

        console.print(table)


@snapshot_app.command("create")
def vm_snapshot_create_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    remark: str = typer.Option("", "--remark", "-m", help="快照备注说明"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """为指定 KVM 虚拟机创建快照 (`/v1/vm/domain/snapshotCreate`)."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(
            client,
            "/v1/vm/domain/snapshotCreate",
            data={"name": vm_name, "remark": remark},
            json_output=json_output,
        )
        if resp is None:
            return
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(f"[bold green]✓[/bold green] 已为虚拟机 [bold]{vm_name}[/bold] 提交创建快照任务 (备注: {remark or '无'})。")


@snapshot_app.command("restore")
def vm_snapshot_restore_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    snapshot_name: str = typer.Argument(..., help="要恢复的快照名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """将指定 KVM 虚拟机恢复到指定快照 (`/v1/vm/domain/snapshotRevert`)."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(
            client,
            "/v1/vm/domain/snapshotRevert",
            data={"name": vm_name, "snapshotName": snapshot_name},
            json_output=json_output,
        )
        if resp is None:
            return
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(
            f"[bold green]✓[/bold green] 已提交将虚拟机 [bold]{vm_name}[/bold] 恢复至快照 [cyan]{snapshot_name}[/cyan] 的任务。"
        )


@snapshot_app.command("rm")
def vm_snapshot_rm_cmd(
    vm_name: str = typer.Argument(..., help="虚拟机名称"),
    snapshot_name: str = typer.Argument(..., help="要删除的快照名称"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出"),
) -> None:
    """删除指定 KVM 虚拟机的快照 (`/v1/vm/domain/snapshotDelete`)."""
    with ZSpaceClient() as client:
        resp = _request_vm_api(
            client,
            "/v1/vm/domain/snapshotDelete",
            data={"name": vm_name, "snapshotName": snapshot_name},
            json_output=json_output,
        )
        if resp is None:
            return
        if json_output:
            console.print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        console.print(
            f"[bold green]✓[/bold green] 已提交删除虚拟机 [bold]{vm_name}[/bold] 快照 [cyan]{snapshot_name}[/cyan] 的任务。"
        )
