"""极空间 (ZSpace NAS) 加密、签名与文件哈希工具模块.

涵盖：
1. RSAES-PKCS1-v1_5 公钥加密（用于 /auth/login 登录与保险箱密码加密）
2. 大文件分片上传会话 uuid MD5 计算（逆向自 background.js setUUID）
3. 快速首尾特征哈希 getFSHash（用于秒传与远端同名校验 /v2/file/hash）
4. 上传完整性校验哈希 calculateFileHash（支持 simple 抽样与 complete 全量模式）
5. vuex.json 加密模式回退解密（AES-256-CBC + scrypt 派生密钥）
"""

from __future__ import annotations

import base64
import hashlib
import math
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def normalize_pem_public_key(pub_key: str) -> bytes:
    """将公钥字符串规范化为标准 PEM 字节串."""
    cleaned = pub_key.strip()
    if not cleaned.startswith("-----BEGIN"):
        # 若为裸 Base64 字符串，包装为 PUBLIC KEY PEM 格式
        cleaned = f"-----BEGIN PUBLIC KEY-----\n{cleaned}\n-----END PUBLIC KEY-----"
    return cleaned.encode("utf-8")


def rsa_encrypt(plain_text: str, pub_key_pem: str, chunk_mode: bool = False) -> str:
    """使用 RSAES-PKCS1-v1_5 公钥加密明文并返回 Base64 密文.

    Args:
        plain_text: 待加密字符串（调用方通常需先 strip 首尾空白）
        pub_key_pem: PEM 格式或 Base64 编码的 RSA 公钥（如 /key/pub 返回的 key）
        chunk_mode: 若为 True，按每 50 字符分段加密并以 '~' 拼接（兼容旧版默认公钥）

    Returns:
        Base64 编码的 RSA 密文字符串
    """
    pem_bytes = normalize_pem_public_key(pub_key_pem)
    public_key = serialization.load_pem_public_key(pem_bytes)
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("提供的公钥不是有效的 RSA 公钥")

    def _encrypt_one(segment: str) -> str:
        cipher_bytes = public_key.encrypt(
            segment.encode("utf-8"),
            padding.PKCS1v15(),
        )
        return base64.b64encode(cipher_bytes).decode("ascii")

    if not chunk_mode:
        return _encrypt_one(plain_text)

    # 分段加密模式：每 50 个字符切分为一段，各段 Base64 以 '~' 拼接
    segments = [plain_text[i : i + 50] for i in range(0, max(len(plain_text), 1), 50)]
    return "~".join(_encrypt_one(seg) for seg in segments)


def calc_upload_uuid(mtime_ms: int | float, file_size: int, target_path: str) -> str:
    """计算大文件分片上传与断点续传的会话 uuid.

    逆向自客户端 background.js 的 setUUID：
    1. 若路径以 /zenith_sata/my/data 开头，先替换为 /my
    2. 将 math.ceil(mtime_ms) 与 file_size 先做数值算术相加
    3. 转为十进制字符串后与目标路径直接拼接，计算 32 位小写 MD5
    """
    norm_path = target_path
    if norm_path.startswith("/zenith_sata/my/data"):
        norm_path = "/my" + norm_path[len("/zenith_sata/my/data") :]
    total_num = int(math.ceil(mtime_ms)) + int(file_size)
    raw = f"{total_num}{norm_path}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def getFSHash(file_path: str | Path) -> str:
    """计算极空间快速首尾特征哈希 (getFSHash).

    逆向自客户端 background.js：
    1. 先将文件字节大小十进制字符串 str(size) 写入 MD5 上下文
    2. 若 size == 0：直接返回 MD5
    3. 若 0 < size <= 2048：读取全部文件字节写入 MD5
    4. 若 size > 2048：依次读取前 1024 字节 [0, 1023] 与最后 1024 字节 [size-1024, size-1] 写入 MD5
    """
    path = Path(file_path)
    size = path.stat().st_size
    md5 = hashlib.md5()
    md5.update(str(size).encode("utf-8"))

    if size == 0:
        return md5.hexdigest()

    with path.open("rb") as fh:
        if size <= 2048:
            md5.update(fh.read())
        else:
            md5.update(fh.read(1024))
            fh.seek(size - 1024)
            md5.update(fh.read(1024))

    return md5.hexdigest()


# 提供蛇形命名别名，方便 Python 风格调用
get_fs_hash = getFSHash


def calculateFileHash(file_path: str | Path, option: str = "simple") -> str:
    """计算极空间上传完整性校验哈希 (calculateFileHash).

    Args:
        file_path: 本地文件路径
        option: 'complete'（全量 MD5）或 'simple'（大文件分段抽样 MD5）
    """
    path = Path(file_path)
    size = path.stat().st_size
    md5 = hashlib.md5()

    mib_10 = 10 * 1024 * 1024
    mib_500 = 500 * 1024 * 1024
    gib_10 = 10 * 1024 * 1024 * 1024
    gib_5 = 5 * 1024 * 1024 * 1024

    with path.open("rb") as fh:
        if option == "complete" or size <= mib_10:
            while chunk := fh.read(1024 * 1024):
                md5.update(chunk)
        elif size <= mib_500:
            # 首、中、尾各 100 KiB (102400 字节)
            chunk_len = 102400
            md5.update(fh.read(chunk_len))
            mid_offset = max(0, (size // 2) - (chunk_len // 2))
            fh.seek(mid_offset)
            md5.update(fh.read(chunk_len))
            fh.seek(max(0, size - chunk_len))
            md5.update(fh.read(chunk_len))
        elif size <= gib_10:
            # 首、中、尾各 1 MiB (1048576 字节)
            chunk_len = 1048576
            md5.update(fh.read(chunk_len))
            mid_offset = max(0, (size // 2) - (chunk_len // 2))
            fh.seek(mid_offset)
            md5.update(fh.read(chunk_len))
            fh.seek(max(0, size - chunk_len))
            md5.update(fh.read(chunk_len))
        else:
            # 超过 10 GiB：每隔 5 GiB 读取 1 MiB
            chunk_len = 1048576
            offset = 0
            while offset < size:
                fh.seek(offset)
                md5.update(fh.read(chunk_len))
                offset += gib_5

    return md5.hexdigest()


calculate_file_hash = calculateFileHash


def decrypt_vuex_bytes(raw_bytes: bytes) -> str:
    """解密加密存储的 vuex.json 内容（若首字节非 '{'）.

    逆向自 app.d3d20628.js：
    使用硬编码密码 '08dbab37279e24852d8f2a11795c143f'，
    经 scrypt(password, salt='salt', dklen=32, N=16384, r=8, p=1) 派生密钥，
    取前 16 字节为 IV 进行 AES-256-CBC 解密并去除 PKCS7 填充。
    """
    if not raw_bytes:
        return "{}"
    # 去除 UTF-8 BOM
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]
    if raw_bytes.lstrip().startswith(b"{"):
        return raw_bytes.decode("utf-8")

    if len(raw_bytes) <= 16:
        raise ValueError("加密的 vuex.json 数据长度不足")

    key = hashlib.scrypt(
        b"08dbab37279e24852d8f2a11795c143f",
        salt=b"salt",
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    iv = raw_bytes[:16]
    cipher_body = raw_bytes[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_body) + decryptor.finalize()
    # 去除 PKCS#7 padding
    pad_len = padded[-1]
    if 1 <= pad_len <= 16:
        padded = padded[:-pad_len]
    return padded.decode("utf-8")
