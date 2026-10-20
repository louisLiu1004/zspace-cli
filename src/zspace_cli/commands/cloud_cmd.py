"""极空间 `zs cloud` 网盘挂载状态（只读，输出已脱敏）.

已在真机验证返回 200 的接口：
`/znetdisk/auth/userinfo`（百度网盘）、`/zdrive/baidu/rclone/mountinfo`、
`/zdrive/gdrive/userinfo`、`/zdrive/gdrive/spaceinfo`。
其它网盘（阿里/115/夸克/天翼/OneDrive 等）本机未绑定，暂未封装。
账号 token、邮箱等敏感字段只会脱敏显示。
"""

from __future__ import annotations

import json

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import call, console, human_bytes, mask, print_json

cloud_app = typer.Typer(
    name="cloud",
    help="网盘绑定与空间状态（百度网盘 / Google Drive，只读，已脱敏）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = cloud_app


def _ok(resp) -> bool:
    return isinstance(resp, dict) and str(resp.get("code")) == "200"


def _baidu() -> dict:
    resp = call("POST", "/znetdisk/auth/userinfo")
    if not _ok(resp):
        return {"provider": "百度网盘", "bound": False}
    d = resp.get("data") or {}
    info, quota = d.get("user_info") or {}, d.get("quota") or {}
    mount = {}
    m = call("POST", "/zdrive/baidu/rclone/mountinfo")
    if _ok(m):
        try:
            mount = json.loads((m.get("data") or {}).get("data") or "{}")
        except ValueError:
            mount = {}
    return {
        "provider": "百度网盘",
        "bound": True,
        "user": info.get("netdisk_name") or mask(info.get("baidu_name"), 1),
        "used": quota.get("used"),
        "total": quota.get("total"),
        "mount": mount.get("mount_point") or "-",
    }


def _gdrive() -> dict:
    resp = call("POST", "/zdrive/gdrive/userinfo")
    if not _ok(resp):
        return {"provider": "Google Drive", "bound": False}
    d = resp.get("data") or {}
    email = str(d.get("emailAddress") or "")
    name, _, domain = email.partition("@")
    space = call("POST", "/zdrive/gdrive/spaceinfo")
    sd = (space.get("data") or {}) if _ok(space) else {}
    return {
        "provider": "Google Drive",
        "bound": True,
        "user": f"{mask(name, 2)}@{domain}" if domain else mask(d.get("displayName"), 1),
        "used": sd.get("usage") or sd.get("used"),
        "total": sd.get("limit") or sd.get("total"),
        "mount": "-",
    }


@cloud_app.command("status")
def status(as_json: bool = typer.Option(False, "--json", help="输出（已脱敏的）JSON")) -> None:
    """各网盘的绑定状态、账号（脱敏）与空间用量."""
    rows = [_baidu(), _gdrive()]
    if as_json:
        print_json(rows)
        return
    t = Table(title="网盘状态", box=box.SIMPLE_HEAVY)
    for col in ("网盘", "状态", "账号", "已用", "总量", "挂载点"):
        t.add_column(col)
    for r in rows:
        if not r["bound"]:
            t.add_row(r["provider"], "[dim]未绑定[/dim]", "-", "-", "-", "-")
        else:
            t.add_row(
                r["provider"],
                "[green]已绑定[/green]",
                str(r["user"]),
                human_bytes(r["used"]) if r["used"] else "-",
                human_bytes(r["total"]) if r["total"] else "-",
                r["mount"],
            )
    console.print(t)
