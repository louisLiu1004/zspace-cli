"""针对 src/zspace_cli/core/catalog.py 接口目录引擎的单元测试."""

from __future__ import annotations

from zspace_cli.core.catalog import clean_catalog_url, get_catalog


def test_catalog_load() -> None:
    """验证内置 API 目录能正常从 parsed_catalog.json 加载，且条目总数大于 1900."""
    catalog = get_catalog()
    assert catalog is not None

    # 验证总条目数大于 1900（实际提取为 2,004 条）
    assert catalog.total_entries > 1900
    assert catalog.total_entries == 2004

    # 验证包含 73 个业务模块
    assert catalog.module_count == 73

    # 验证去重后的唯一 URL 路由数合理
    assert catalog.unique_url_count > 1000

    # 验证全局统计概览结构
    summary = catalog.get_summary_counts()
    assert summary["modules"] == 73
    assert summary["total_entries"] > 1900
    assert "GET" in summary["by_method"]
    assert "POST" in summary["by_method"]

    # 验证模块统计明细
    module_stats = catalog.get_module_stats()
    assert len(module_stats) == 73
    # 验证排序首位是接口数最多的模块
    assert module_stats[0].entry_count >= module_stats[-1].entry_count


def test_catalog_search_thunder() -> None:
    """验证搜索 "thunder" 能命中 /thunder/getWebUrl 接口."""
    catalog = get_catalog()
    hits = catalog.search("thunder")
    assert len(hits) > 0

    hit_urls = [entry.url for entry in hits]
    assert "/thunder/getWebUrl" in hit_urls

    # 进一步验证命中的 ApiEntry 字段属性
    thunder_entry = next(e for e in hits if e.url == "/thunder/getWebUrl")
    assert thunder_entry.fn == "getThunderUrl"
    assert thunder_entry.method in ("GET", "POST")
    assert thunder_entry.module_id != ""
    assert thunder_entry.module_name != ""


def test_catalog_resolve() -> None:
    """验证 resolve 精确或模糊定位单个 API 接口."""
    catalog = get_catalog()

    # 1. 完整 URL 定位
    resolved_full = catalog.resolve("/thunder/getWebUrl")
    assert len(resolved_full) > 0
    assert resolved_full[0].url == "/thunder/getWebUrl"

    # 2. 缺失前导斜杠的 URL 容错定位
    resolved_no_slash = catalog.resolve("thunder/getWebUrl")
    assert len(resolved_no_slash) > 0
    assert resolved_no_slash[0].url == "/thunder/getWebUrl"

    # 3. 不存在的接口定位返回空列表
    assert catalog.resolve("/non_existent/path/12345") == []


def test_clean_catalog_url() -> None:
    """测试 clean_catalog_url 对静态分析提取出的动态表达式与残余符号的清洗."""
    # 双引号与括号残留
    assert clean_catalog_url('"/zspool/external/rename")') == "/zspool/external/rename"

    # 纯引号包裹
    assert clean_catalog_url('"/v2/file/list"') == "/v2/file/list"

    # 模板前缀清洗
    assert clean_catalog_url("{{d}}/v2/ud/key/pub") == "/v2/ud/key/pub"

    # 三元条件表达式提取末尾实际 URL
    ternary = 't.isDocker?"/zdocker/AppStore/v1/pkg/restart":"/AppStore/v1/pkg/restart"'
    assert clean_catalog_url(ternary) == "/AppStore/v1/pkg/restart"
