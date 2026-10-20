"""针对 src/zspace_cli/core/crypto.py 纯算法函数的单元测试."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from zspace_cli.core.crypto import (
    calc_upload_uuid,
    calculate_file_hash,
    calculateFileHash,
    decrypt_vuex_bytes,
    get_fs_hash,
    getFSHash,
    normalize_pem_public_key,
    rsa_encrypt,
)


def test_calc_upload_uuid() -> None:
    """测试大文件分片上传 uuid 的 MD5 计算逻辑.

    算法规则（逆向自 background.js setUUID）：
    1. 前缀替换：以 '/zenith_sata/my/data' 开头的路径替换为 '/my'
    2. 数值求和：math.ceil(mtime_ms) + int(file_size)
    3. 字符串拼接求 MD5：f"{total_num}{norm_path}"
    """
    # 场景 1：普通路径，浮点毫秒时间戳向上取整
    mtime_ms = 1600000000123.4
    file_size = 1000
    target_path = "/test/upload/sample.txt"
    total_num = math.ceil(mtime_ms) + file_size  # 1600000000124 + 1000 = 1600000001124
    raw_str = f"{total_num}{target_path}"
    expected_md5 = hashlib.md5(raw_str.encode("utf-8")).hexdigest()

    assert calc_upload_uuid(mtime_ms, file_size, target_path) == expected_md5

    # 场景 2：带 /zenith_sata/my/data 前缀的路径归一化
    special_path = "/zenith_sata/my/data/photos/trip.jpg"
    normalized_path = "/my/photos/trip.jpg"
    mtime_int = 1700000000000
    file_size_2 = 2048
    total_num_2 = mtime_int + file_size_2
    expected_md5_2 = hashlib.md5(f"{total_num_2}{normalized_path}".encode("utf-8")).hexdigest()

    assert calc_upload_uuid(mtime_int, file_size_2, special_path) == expected_md5_2


def test_get_fs_hash_empty_file(tmp_path: Path) -> None:
    """测试 getFSHash 处理空文件 (size == 0) 的情况.

    算法规则：先将 str(size) 写入 MD5，若 size == 0 直接返回 MD5。
    对于空文件，相当于 hashlib.md5(b"0").hexdigest()。
    """
    empty_file = tmp_path / "empty.bin"
    empty_file.write_bytes(b"")

    expected = hashlib.md5(b"0").hexdigest()
    assert getFSHash(empty_file) == expected
    assert get_fs_hash(empty_file) == expected


def test_get_fs_hash_small_file(tmp_path: Path) -> None:
    """测试 getFSHash 处理小文件 (0 < size <= 2048) 的情况.

    算法规则：MD5 先写入 str(size)，再写入全部文件内容。
    """
    small_file = tmp_path / "small.txt"
    content = b"hello zspace fshash"
    small_file.write_bytes(content)

    size = len(content)
    expected = hashlib.md5(str(size).encode("utf-8") + content).hexdigest()
    assert getFSHash(small_file) == expected
    assert get_fs_hash(small_file) == expected


def test_get_fs_hash_large_file(tmp_path: Path) -> None:
    """测试 getFSHash 处理大文件 (size > 2048) 的首尾采样情况.

    算法规则：MD5 先写入 str(size)，再写入前 1024 字节，最后写入末尾 1024 字节。
    """
    large_file = tmp_path / "large.bin"
    head = b"H" * 1024
    middle = b"M" * 952
    tail = b"T" * 1024
    full_content = head + middle + tail  # 总大小 3000 字节
    large_file.write_bytes(full_content)

    expected = hashlib.md5(b"3000" + head + tail).hexdigest()
    assert getFSHash(large_file) == expected
    assert get_fs_hash(large_file) == expected


def test_calculate_file_hash_complete_and_simple(tmp_path: Path) -> None:
    """测试 calculateFileHash 的完整校验与小文件抽样校验."""
    test_file = tmp_path / "data.bin"
    content = b"file content for calculateFileHash"
    test_file.write_bytes(content)

    expected_full_md5 = hashlib.md5(content).hexdigest()
    # 当文件小于 10MB 时，simple 模式与 complete 模式行为完全一致（全量读取）
    assert calculateFileHash(test_file, option="complete") == expected_full_md5
    assert calculateFileHash(test_file, option="simple") == expected_full_md5
    assert calculate_file_hash(test_file) == expected_full_md5


def test_rsa_encrypt_and_normalize_pem() -> None:
    """测试 RSAES-PKCS1-v1_5 加密纯函数与 PEM 公钥格式标准化."""
    # 生成测试用的 RSA 2048 密钥对
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    pem_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pem_str = pem_bytes.decode("utf-8")

    # 1. 测试裸 Base64 格式的公钥能否正确补齐 PEM 头尾
    bare_b64 = pem_str.replace("-----BEGIN PUBLIC KEY-----", "").replace("-----END PUBLIC KEY-----", "").strip()
    normalized = normalize_pem_public_key(bare_b64)
    assert normalized.startswith(b"-----BEGIN PUBLIC KEY-----")
    assert normalized.endswith(b"-----END PUBLIC KEY-----")

    # 2. 单段加密测试（常规登录密码等较短文本）
    plain_text = "my_secure_password_123"
    cipher_b64 = rsa_encrypt(plain_text, pem_str, chunk_mode=False)
    assert "~" not in cipher_b64

    # 用私钥解密验证
    import base64
    decrypted_bytes = private_key.decrypt(
        base64.b64decode(cipher_b64),
        asym_padding.PKCS1v15(),
    )
    assert decrypted_bytes.decode("utf-8") == plain_text

    # 3. 分段加密模式测试（长度大于 50 字符时按 50 字符分块并以 ~ 连接）
    long_plain = "A" * 60 + "B" * 60
    chunk_cipher = rsa_encrypt(long_plain, pem_str, chunk_mode=True)
    parts = chunk_cipher.split("~")
    assert len(parts) == 3  # 60+60=120 字符分为 50, 50, 20 三段

    decrypted_chunks = [
        private_key.decrypt(base64.b64decode(part), asym_padding.PKCS1v15()).decode("utf-8")
        for part in parts
    ]
    assert "".join(decrypted_chunks) == long_plain


def test_decrypt_vuex_bytes() -> None:
    """测试 vuex.json 本地凭证解密函数."""
    # 1. 空数据返回 "{}"
    assert decrypt_vuex_bytes(b"") == "{}"

    # 2. 未加密的明文 JSON，直接返回 UTF-8 字符串
    plain_json = b'{"token": "demo_token_123"}'
    assert decrypt_vuex_bytes(plain_json) == '{"token": "demo_token_123"}'

    # 3. 带 UTF-8 BOM 的明文 JSON
    bom_json = b"\xef\xbb\xbf" + plain_json
    assert decrypt_vuex_bytes(bom_json) == '{"token": "demo_token_123"}'

    # 4. 长度不足 16 字节的非明文数据应抛出 ValueError
    with pytest.raises(ValueError, match="加密的 vuex.json 数据长度不足"):
        decrypt_vuex_bytes(b"short_cipher")

    # 5. 按照算法规则构造 AES-256-CBC 密文，验证解密还原
    key = hashlib.scrypt(
        b"08dbab37279e24852d8f2a11795c143f",
        salt=b"salt",
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    iv = b"1234567890abcdef"
    payload = b'{"encrypted_user": "admin"}'

    # PKCS#7 填充
    padder = sym_padding.PKCS7(128).padder()
    padded_data = padder.update(payload) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    # 将 IV 与密文拼接后解密
    encrypted_blob = iv + ciphertext
    decrypted_str = decrypt_vuex_bytes(encrypted_blob)
    assert decrypted_str == '{"encrypted_user": "admin"}'
