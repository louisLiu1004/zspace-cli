"""极空间 `zs smb` 文件共享协议状态汇总（只读）.

已在真机验证返回 200 的接口：samba / ftp / webdav / dlna 的 `status`。
NFS、SFTP、iSCSI 等在本机探测时未返回有效数据，暂未封装。
"""

from __future__ import annotations

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import call, console, print_json, yes_no

smb_app = typer.Typer(
    name="smb",
    help="文件共享协议开关与端口汇总（Samba / FTP / WebDAV / DLNA，只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = smb_app

_BASE = "/api/fileshare_service"


def _detail(name: str, d: dict) -> str:
    if name == "samba":
        return f"主机名 {d.get('host_name')}，访客 {'允许' if d.get('guest') else '禁止'}"
    if name == "ftp":
        return f"端口 {d.get('port')}，被动模式 {'开' if d.get('passive') else '关'}"
    if name == "webdav":
        return f"HTTP {d.get('http_port')} / HTTPS {d.get('https_port')}"
    if name == "dlna":
        return f"媒体共享 {'开' if d.get('is_share') else '关'}"
    return "-"


@smb_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """汇总各文件共享协议的开启状态与端口."""
    result: dict[str, dict] = {}
    for name in ("samba", "ftp", "webdav", "dlna"):
        resp = call("POST", f"{_BASE}/{name}/status")
        ok = isinstance(resp, dict) and str(resp.get("code")) == "200"
        result[name] = (resp.get("data") or {}) if ok else {}
        result[name]["_ok"] = ok

    if as_json:
        print_json(result)
        return

    t = Table(title="文件共享协议", box=box.SIMPLE_HEAVY)
    for col in ("协议", "状态", "详情"):
        t.add_column(col)
    for name, d in result.items():
        if not d.pop("_ok"):
            t.add_row(name, "[yellow]查询失败[/yellow]", "-")
        else:
            t.add_row(name, yes_no(d.get("status")), _detail(name, d))
    console.print(t)
