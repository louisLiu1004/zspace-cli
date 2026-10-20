"""针对 src/zspace_cli/commands/_common.py 辅助工具函数的单元测试."""

from __future__ import annotations

import time

from zspace_cli.commands._common import human_bytes, human_time, mask, yes_no


def test_human_bytes() -> None:
    """测试 human_bytes 字节格式化函数."""
    # 零与常规字节
    assert human_bytes(0) == "0 B"
    assert human_bytes(512) == "512 B"

    # 千字节 (KB)
    assert human_bytes(1024) == "1.00 KB"
    assert human_bytes(1536) == "1.50 KB"

    # 兆字节 (MB)
    assert human_bytes(1024 * 1024) == "1.00 MB"

    # 吉字节 (GB)
    assert human_bytes(1024 * 1024 * 1024) == "1.00 GB"

    # 太字节 (TB)
    assert human_bytes(1024**4) == "1.00 TB"

    # 拍字节 (PB) 及超大容量边界
    assert human_bytes(1024**5) == "1.00 PB"
    assert human_bytes(2.5 * (1024**5)) == "2.50 PB"

    # 负数值
    assert human_bytes(-1024) == "-1.00 KB"

    # 异常输入处理
    assert human_bytes(None) == "0 B"
    assert human_bytes("") == "0 B"
    assert human_bytes("invalid_number") == "-"


def test_human_time() -> None:
    """测试 human_time 时间戳格式化函数."""
    # 非法或无效时间戳返回 "-"
    assert human_time(0) == "-"
    assert human_time(-1) == "-"
    assert human_time(None) == "-"
    assert human_time("invalid") == "-"
    assert human_time("") == "-"

    # 有效时间戳应与本地时区格式化结果一致
    ts = 1700000000
    expected = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    assert human_time(ts) == expected
    assert human_time(str(ts)) == expected


def test_mask() -> None:
    """测试 mask 脱敏函数."""
    # 空值处理
    assert mask("") == ""
    assert mask(None) == ""

    # 长度小于等于保留位数 2 倍 (keep*2) 时全遮盖
    assert mask("123", keep=3) == "***"
    assert mask("123456", keep=3) == "******"

    # 长度大于 keep*2 时保留首尾各 keep 位字符，中间用 *** 替换
    assert mask("1234567", keep=3) == "123***567"
    assert mask("12345678", keep=2) == "12***78"
    assert mask("my_secret_token_123456", keep=4) == "my_s***3456"

    # 非字符串类型自动转换为字符串
    assert mask(123456789, keep=3) == "123***789"


def test_yes_no() -> None:
    """测试 yes_no 状态富文本标签转换函数."""
    assert yes_no(True) == "[green]开启[/green]"
    assert yes_no(False) == "[dim]关闭[/dim]"
    assert yes_no(1) == "[green]开启[/green]"
    assert yes_no(0) == "[dim]关闭[/dim]"
