"""极空间 `zs net` 防火墙、登录防护与证书查看（均为只读）.

所用接口已在真机验证返回 200：
`/v1/zfirewall/info`、`/ip_access/get_limit_config`、`/cert/info`。
"""

from __future__ import annotations

import typer
from rich import box
from rich.table import Table

from zspace_cli.commands._common import call_data, console, human_time, print_json, yes_no

net_app = typer.Typer(
    name="net",
    help="防火墙、登录防护与证书（只读）",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app = net_app


@net_app.command("firewall")
def firewall(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """防火墙开关、各网口默认策略与放行规则."""
    data = call_data("GET", "/v1/zfirewall/info")
    if as_json:
        print_json(data)
        return
    console.print(f"防火墙：{yes_no(data.get('enable') == 'yes')}  (设备支持：{data.get('support')})")

    t = Table(title="网口默认策略", box=box.SIMPLE_HEAVY)
    for col in ("名称", "类型", "启用", "默认动作"):
        t.add_column(col)
    for i in data.get("interfaces", []):
        t.add_row(str(i.get("name")), str(i.get("type")), "是" if i.get("able") else "否", str(i.get("default_action")))
    console.print(t)

    rules = data.get("rules") or []
    if rules:
        r = Table(title="规则", box=box.SIMPLE_HEAVY)
        for col in ("服务", "协议", "动作", "端口", "生效"):
            r.add_column(col)
        for rule in rules:
            info = (rule.get("service_info") or [{}])[0]
            r.add_row(
                str(info.get("description_cn") or rule.get("service")),
                str(rule.get("protocol")),
                str(rule.get("action")),
                str(info.get("dports", "-")),
                "是" if rule.get("is_active") else "否",
            )
        console.print(r)


@net_app.command("login-guard")
def login_guard(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """登录失败锁定策略（IP 访问控制）."""
    data = call_data("POST", "/ip_access/get_limit_config")
    if as_json:
        print_json(data)
        return
    console.print(
        f"登录防护：{yes_no(data.get('status'))}\n"
        f"规则：{data.get('minute')} 分钟内失败 {data.get('num')} 次即锁定；"
        f"{data.get('auto_unlock_num')} 分钟后自动解锁；锁定用户：{'是' if data.get('is_lock_user') else '否'}"
    )


@net_app.command("cert")
def cert(as_json: bool = typer.Option(False, "--json", help="输出原始 JSON")) -> None:
    """SSL 证书列表与有效期."""
    data = call_data("POST", "/cert/info")
    if as_json:
        print_json(data)
        return
    t = Table(title="SSL 证书", box=box.SIMPLE_HEAVY)
    for col in ("ID", "颁发者", "开始", "到期", "状态"):
        t.add_column(col, overflow="fold")
    for c in data or []:
        t.add_row(
            str(c.get("id")),
            str(c.get("issuer_o")),
            human_time(c.get("start_date")),
            human_time(c.get("end_date")),
            "有效" if c.get("status") == 1 else str(c.get("status")),
        )
    console.print(t)
