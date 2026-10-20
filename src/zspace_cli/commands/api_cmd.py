"""极空间 `zs api` 子命令模块：全量 73 模块 / 2,004 接口检索、详情查看与动态调用引擎."""

from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from zspace_cli.core.catalog import ApiEntry, get_catalog
from zspace_cli.core.client import ZSpaceClient, ZSpaceError

api_app = typer.Typer(
    name="api",
    help="全量 2,004 个极空间 API 接口字典检索、参数查询与动态调用引擎",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _parse_kv_params(raw_params: list[str] | None, as_json_types: bool = False) -> dict[str, Any]:
    """解析命令行传入的 `-p key=val` 参数列表.

    支持：
    - 重复同名键（如 `-p paths=/a -p paths=/b`）自动聚合为列表
    - `key[]=val` 自动识别为列表参数
    - 当 `as_json_types=True` 时，尝试将数字、布尔、JSON 数组/对象自动转型
    """
    result: dict[str, Any] = {}
    if not raw_params:
        return result

    for item in raw_params:
        if "=" not in item:
            raise typer.BadParameter(f"参数格式错误: '{item}'，请使用 -p key=value 格式")
        key, val_str = item.split("=", 1)
        key = key.strip()

        parsed_val: Any = val_str
        if as_json_types:
            low = val_str.strip().lower()
            if low == "true":
                parsed_val = True
            elif low == "false":
                parsed_val = False
            elif low == "null":
                parsed_val = None
            elif val_str.strip().startswith(("[", "{")):
                try:
                    parsed_val = json.loads(val_str)
                except ValueError:
                    parsed_val = val_str
            else:
                try:
                    parsed_val = int(val_str)
                except ValueError:
                    try:
                        parsed_val = float(val_str)
                    except ValueError:
                        parsed_val = val_str

        if key.endswith("[]"):
            result.setdefault(key, []).append(parsed_val)
        elif key in result:
            existing = result[key]
            if isinstance(existing, list):
                existing.append(parsed_val)
            else:
                result[key] = [existing, parsed_val]
        else:
            result[key] = parsed_val

    return result


@api_app.command("stats")
def stats_cmd(
    top: int = typer.Option(73, "--top", "-n", help="展示前 N 个模块（默认展示全部 73 个模块）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出统计数据"),
) -> None:
    """统计展示内置的 73 个 Webpack 业务模块与 2,004 个 API 分布."""
    catalog = get_catalog()
    summary = catalog.get_summary_counts()
    mod_stats = catalog.get_module_stats()

    if json_output:
        payload = {
            **summary,
            "module_list": [
                {
                    "module_id": m.module_id,
                    "module_name": m.module_name,
                    "js_file": m.js_file,
                    "entry_count": m.entry_count,
                    "methods": m.methods,
                    "sample_urls": m.sample_urls,
                }
                for m in mod_stats[:top]
            ],
        }
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # 1. 顶部全局汇总面板
    methods_str = " | ".join(
        f"[bold]{k}[/bold]: {v}"
        for k, v in sorted(summary["by_method"].items(), key=lambda x: -x[1])
    )
    ct_str = " | ".join(
        f"[bold]{k}[/bold]: {v}"
        for k, v in sorted(summary["by_content_type"].items(), key=lambda x: -x[1])
    )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column()
    grid.add_row("内置模块总数:", f"[bold green]{summary['modules']}[/bold green] 个独立业务模块")
    grid.add_row("收录 API 总数:", f"[bold green]{summary['total_entries']}[/bold green] 个接口条目（唯一路由: {summary['unique_urls']} 个）")
    grid.add_row("HTTP 方法分布:", methods_str)
    grid.add_row("请求格式分布:", ct_str)

    console.print(
        Panel(
            grid,
            title="[bold]极空间全量 API 目录统计概览[/bold]",
            border_style="cyan",
        )
    )

    # 2. 73 个模块明细表格
    table = Table(
        title=f"业务模块 API 分布明细 (共 {len(mod_stats)} 个模块)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("模块 ID", style="bold yellow", width=8)
    table.add_column("业务领域名称", style="bold")
    table.add_column("接口数", justify="right", style="bold green", width=7)
    table.add_column("方法构成", width=18)
    table.add_column("代表性 API 路由示例", style="dim")

    for idx, m in enumerate(mod_stats[:top], start=1):
        m_parts = [f"{k}:{v}" for k, v in sorted(m.methods.items(), key=lambda x: -x[1])]
        sample_str = ", ".join(m.sample_urls[:2])
        table.add_row(
            str(idx),
            m.module_id,
            m.module_name,
            str(m.entry_count),
            " ".join(m_parts),
            sample_str,
        )

    console.print(table)


@api_app.command("search")
def search_cmd(
    keyword: str = typer.Argument(..., help="搜索关键词（匹配 URL 路径、前端函数名、模块名或参数名）"),
    module_id: Optional[str] = typer.Option(None, "--module", "-m", help="按模块 ID 过滤（如 83787）"),
    method: Optional[str] = typer.Option(None, "--method", "-X", help="按 HTTP 方法过滤（POST/GET）"),
    limit: int = typer.Option(50, "--limit", "-n", help="最大返回条数（传 0 返回全部匹配）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出搜索结果"),
) -> None:
    """在 2,004 个接口中按 URL、前端函数名或参数名模糊搜索并用 Rich 表格展示."""
    catalog = get_catalog()
    results = catalog.search(keyword, module_id=module_id, method=method, limit=limit)

    if json_output:
        console.print(
            json.dumps(
                [
                    {
                        "module_id": e.module_id,
                        "module_name": e.module_name,
                        "fn": e.fn,
                        "method": e.method,
                        "url": e.url,
                        "content_type": e.display_content_type,
                        "api_target": e.api_target,
                        "params": e.params,
                    }
                    for e in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not results:
        console.print(f"[yellow]未找到与 '{keyword}' 匹配的 API 接口。[/yellow]")
        raise typer.Exit(0)

    table = Table(
        title=f"API 搜索结果: '{keyword}' (匹配展示 {len(results)} 条)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("模块", style="yellow", width=22)
    table.add_column("方法", width=6)
    table.add_column("API 路由 (URL)", style="bold green")
    table.add_column("前端函数名 (fn)", style="cyan")
    table.add_column("格式", width=15)
    table.add_column("推导参数 (params)", style="dim")

    for e in results:
        method_badge = (
            f"[bold green]{e.method}[/bold green]"
            if e.method == "GET"
            else f"[bold blue]{e.method}[/bold blue]"
        )
        short_ct = (
            "json"
            if e.is_json
            else ("multipart" if e.is_multipart else ("query" if e.method == "GET" else "form"))
        )
        if e.api_target:
            short_ct += f" ({e.api_target})"
        params_str = ", ".join(e.params[:6])
        if len(e.params) > 6:
            params_str += f" (+{len(e.params) - 6})"

        mod_cell = f"[{e.module_id}] {e.module_name.split(' ')[0]}"
        table.add_row(
            mod_cell,
            method_badge,
            e.url,
            e.fn,
            short_ct,
            params_str or "-",
        )

    console.print(table)
    console.print(
        "[dim]提示: 使用 `zs api info <URL或函数名>` 查看详情，或使用 `zs api call <URL> -p key=val` 直接调用。[/dim]"
    )


@api_app.command("info")
def info_cmd(
    url_or_fn: str = typer.Argument(..., help="API 路由路径（如 /zspool/info）或前端函数名（如 getZspoolInfo）"),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 格式输出接口定义"),
) -> None:
    """查看某个具体 API 的请求方法、URL、Content-Type、所属模块、推导参数列表."""
    catalog = get_catalog()
    matches = catalog.resolve(url_or_fn)

    if not matches:
        console.print(f"[red]! 未找到匹配的 API 接口: {url_or_fn}[/red]")
        raise typer.Exit(1)

    # 若存在精确匹配则优先展示精确匹配项
    norm = url_or_fn.strip().lower()
    exact = [
        m
        for m in matches
        if m.url.lower() in (norm, f"/{norm}")
        or m.fn.lower() == norm
        or m.qualified_fn.lower() == norm
    ]
    display_list: list[ApiEntry] = exact if exact else matches[:5]

    if json_output:
        console.print(
            json.dumps(
                [
                    {
                        "module_id": e.module_id,
                        "module_name": e.module_name,
                        "js_file": e.js_file,
                        "fn": e.fn,
                        "qualified_fn": e.qualified_fn,
                        "method": e.method,
                        "url": e.url,
                        "content_type": e.display_content_type,
                        "api_target": e.api_target or "direct",
                        "params": e.params,
                    }
                    for e in display_list
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    for e in display_list:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column()

        grid.add_row("API 路由 (URL):", f"[bold green]{e.url}[/bold green]")
        grid.add_row("HTTP 方法:", f"[bold]{e.method}[/bold]")
        grid.add_row("请求 Content-Type:", e.display_content_type)
        grid.add_row("目标网关 (Target):", e.api_target or "direct (NAS 本地代理 / 局域网直连)")
        grid.add_row("所属业务模块:", f"[yellow]{e.module_id}[/yellow] - {e.module_name}")
        grid.add_row("前端源码 Chunk:", f"[dim]{e.js_file}[/dim]")
        grid.add_row("前端函数名 (fn):", f"[bold cyan]{e.fn}[/bold cyan]  [dim](限定名: {e.qualified_fn})[/dim]")

        if e.params:
            params_formatted = ", ".join(f"[bold]{p}[/bold]" for p in e.params)
            grid.add_row(f"推导业务参数 ({len(e.params)}):", params_formatted)
        else:
            grid.add_row("推导业务参数:", "[dim]无必传业务参数（仅需公共鉴权参数）[/dim]")

        # 生成示例调用命令
        sample_cmd = f"zs api call {e.url}"
        if e.params:
            sample_args = " ".join(f"-p {p}=<值>" for p in e.params[:3])
            sample_cmd += f" {sample_args}"
        grid.add_row("快速调用示例:", f"[bold magenta]{sample_cmd}[/bold magenta]")

        console.print(
            Panel(
                grid,
                title=f"[bold]API 接口详情: {e.url}[/bold]",
                border_style="green",
            )
        )


@api_app.command("call")
def call_cmd(
    url_or_fn: str = typer.Argument(
        ...,
        help="要调用的 API 路由路径（如 /zspool/info）或模块函数名（如 83787.getZspoolInfo）",
    ),
    param: Optional[list[str]] = typer.Option(
        None,
        "--param",
        "-p",
        help="请求业务参数，格式为 key=value（可多次指定，如 -p path=/sata11/my/data -p num=20）",
    ),
    json_body: Optional[str] = typer.Option(
        None,
        "--json-body",
        "-d",
        help="直接指定 JSON 请求体字符串（自动以 application/json 发送）",
    ),
    method: Optional[str] = typer.Option(
        None,
        "--method",
        "-X",
        help="强制指定 HTTP 请求方法（如 POST / GET / PUT / DELETE）",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="输出纯 JSON 字符串（不打印額外装饰面板，便于 jq 管道处理）",
    ),
) -> None:
    """直接调用任意极空间 API 接口并格式化输出返回结果."""
    catalog = get_catalog()
    matches = catalog.resolve(url_or_fn)
    matched_entry: ApiEntry | None = matches[0] if matches else None

    is_json_mode = bool(json_body) or (matched_entry is not None and matched_entry.is_json)
    parsed_params = _parse_kv_params(param, as_json_types=is_json_mode)

    parsed_json_body: Any = None
    if json_body:
        try:
            parsed_json_body = json.loads(json_body)
        except ValueError as exc:
            console.print(f"[red]! --json-body 不是合法的 JSON 字符串:[/red] {exc}")
            raise typer.Exit(1)

    with ZSpaceClient() as client:
        try:
            entry, resp_data = catalog.call_by_catalog(
                client=client,
                url_or_fn=url_or_fn,
                params=parsed_params,
                json_body=parsed_json_body,
                method_override=method,
                check_code=False,
            )
        except ZSpaceError as exc:
            if json_output:
                console.print(json.dumps(exc.payload or {"code": exc.code, "msg": exc.msg}, ensure_ascii=False, indent=2))
            else:
                console.print(f"[red]! 接口返回错误:[/red] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            if json_output:
                console.print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            else:
                console.print(f"[red]! 调用失败:[/red] {exc}")
            raise typer.Exit(1)

        formatted_json = json.dumps(resp_data, ensure_ascii=False, indent=2)
        if json_output:
            # 纯 JSON 输出，方便管道与自动化脚本消费
            print(formatted_json)
            return

        if entry:
            header_text = (
                f"[bold green]{method or entry.method}[/bold green] [bold]{entry.url}[/bold]  "
                f"[dim](模块: [{entry.module_id}] {entry.module_name} | 函数: {entry.fn})[/dim]"
            )
        else:
            header_text = f"[bold green]{method or 'POST'}[/bold green] [bold]{url_or_fn}[/bold]  [dim](自定义路由直调)[/dim]"

        console.print(header_text)
        console.print(Syntax(formatted_json, "json", theme="monokai", word_wrap=True))
