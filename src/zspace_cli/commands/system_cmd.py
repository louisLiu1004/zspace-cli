"""极空间 `zs sys` 系统状态、版本、UPS、固件更新与硬盘休眠诊断（均为只读）.

所用接口已在真机验证返回 200：
`/system/polling2`、`/system/home`、`/system/versions`、`/ups/info`、
`/upgrade/info`、`/disk/sleep_check`。
重启/关机等写操作未做封装，需要时可用 `zs api call` 自行调用。
"""

from __future__ import annotations

import time
from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from zspace_cli.commands._common import (
    call_data,
    console,
    human_bytes,
    print_json,
    yes_no,
)

sys_app = typer.Typer(
    name="sys",
    help="系统状态、版本、UPS、固件更新与硬盘休眠诊断（只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = sys_app


def _fmt_uptime(boot_ts: int) -> str:
    secs = max(0, int(time.time()) - int(boot_ts))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d} 天 {h} 小时 {m} 分钟"


@sys_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """系统概览：机型、运行时间、总容量与已用容量、存储池挂载状态."""
    polling = call_data("POST", "/system/polling2")
    home = call_data("POST", "/system/home")
    if as_json:
        print_json({"polling": polling, "home": home})
        return

    total, used = home.get("total", 0), home.get("used", 0)
    pct = f"{used / total * 100:.1f}%" if total else "-"
    uptime = _fmt_uptime(polling.get("uptime_abs", 0)) if polling.get("uptime_abs") else "-"
    body = (
        f"机型            [bold]{home.get('product') or polling.get('product')}[/bold]\n"
        f"运行时间        {uptime}\n"
        f"存储服务        {polling.get('storage_server')}  (存储池协议 {polling.get('sp_ver')})\n"
        f"数据库状态      {polling.get('db_state')}\n"
        f"总容量 / 已用   {human_bytes(total)} / {human_bytes(used)}  ({pct})\n"
        f"用户数          {home.get('user_cnt')}\n"
        f"PC 端版本       {polling.get('pcversion')}"
    )
    console.print(Panel(body, title="极空间系统概览", border_style="cyan"))

    pools = polling.get("pools") or []
    if pools:
        t = Table(title="存储池挂载状态", box=box.SIMPLE_HEAVY)
        for col in ("标识", "名称", "已挂载", "权限"):
            t.add_column(col)
        for p in pools:
            t.add_row(str(p.get("key")), str(p.get("name")), "是" if p.get("mounted") else "否", str(p.get("perm")))
        console.print(t)


@sys_app.command("versions")
def versions(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """系统与各内置组件版本（系统、极相册、极影视、极下载等）."""
    data = call_data("POST", "/system/versions")
    if as_json:
        print_json(data)
        return
    t = Table(title="系统与组件版本", box=box.SIMPLE_HEAVY)
    for col in ("组件", "标识", "版本"):
        t.add_column(col)
    for m in data.get("module", []):
        t.add_row(str(m.get("name")), str(m.get("key")), str(m.get("version")))
    console.print(t)


@sys_app.command("ups")
def ups(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """UPS 电源状态与断电关机设置."""
    data = call_data("GET", "/ups/info")
    if as_json:
        print_json(data)
        return
    info = data.get("ups_info") or {}
    setting, st = info.get("ups_setting") or {}, info.get("ups_status") or {}
    wait = setting.get("timewaittoshutdown")
    console.print(
        Panel(
            f"UPS 联动        {yes_no(setting.get('switch') == 'on')}  (接入方式 {info.get('ups_mode')})\n"
            f"断电后关机等待  {'未设置' if wait in (None, -1) else f'{wait} 秒'}\n"
            f"型号            {st.get('ups_model') or '未检测到 UPS'}\n"
            f"电量 / 续航     {st.get('battery_charge')}% / {st.get('battery_runtime')} 秒\n"
            f"状态            {st.get('ups_status') or '-'}",
            title="UPS 状态",
            border_style="cyan",
        )
    )


@sys_app.command("upgrade")
def upgrade(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """固件/服务升级信息与自动升级开关."""
    data = call_data("GET", "/upgrade/info")
    if as_json:
        print_json(data)
        return
    console.print(f"自动升级：{yes_no((data.get('conf') or {}).get('auto_upgrade'))}")
    t = Table(title="当前版本", box=box.SIMPLE_HEAVY)
    for col in ("类型", "应用 ID", "版本"):
        t.add_column(col)
    for v in data.get("version", []):
        t.add_row(str(v.get("type")), str(v.get("app_id")), str(v.get("app_version")))
    console.print(t)


@sys_app.command("sleep-check")
def sleep_check(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """诊断阻止硬盘休眠的因素，并给出官方建议."""
    data = call_data("POST", "/disk/sleep_check")
    items = data.get("list") or []
    if as_json:
        print_json(items)
        return
    if not items:
        console.print("[green]未发现阻止硬盘休眠的因素[/green]")
        return
    t = Table(title="阻止硬盘休眠的因素", box=box.SIMPLE_HEAVY)
    for col in ("因素", "原因", "建议"):
        t.add_column(col, overflow="fold")
    for it in items:
        t.add_row(str(it.get("title")), str(it.get("reason")), str(it.get("advice")))
    console.print(t)
