"""极空间 (ZSpace NAS) 本地配置与鉴权凭证加载模块.

自动从极空间桌面客户端配置目录（macOS: ~/Library/Application Support/zspace/）
读取 vuex.json、zspace.conf 及 proxy/log.txt，并支持环境变量（ZSPACE_TOKEN、
ZSPACE_HOST 等）与 CLI 独立会话文件（~/.config/zspace-cli/session.json）覆盖。
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zspace_cli.core.crypto import decrypt_vuex_bytes

# 默认本地代理端口与 Web 协议版本号
DEFAULT_LOCAL_PORT = 13579
DEFAULT_NAS_HTTP_PORT = 5055
DEFAULT_NAS_DATA_PORT = 8050
DEFAULT_WEB_VERSION = "2.3.2026042401"
DEFAULT_APP_VERSION = "1.0"
CLOUD_API_BASE = "https://api.zenithspace.net"
SHARE_API_BASE = "https://znas.cn"
TRACKER_API_URL = "https://proxy.zenithspace.net/tracker/query"

# CLI 独立登录会话保存路径
CLI_SESSION_PATH = Path.home() / ".config" / "zspace-cli" / "session.json"

# 局域网 IP:5055 匹配正则（用于从 proxy/log.txt 提取最近直连 IP）
_LAN_ENDPOINT_RE = re.compile(
    r"((?:192\.168|10\.\d+|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+):505[05]"
)


def mask_secret(value: str, head: int = 6, tail: int = 4) -> str:
    """对 Token、Sign 等敏感字符串做脱敏展示."""
    if not value:
        return "(空)"
    if len(value) <= head + tail:
        return value[:2] + "***"
    return f"{value[:head]}...{value[-tail:]}"


@dataclass
class ZSpaceConfig:
    """极空间客户端配置与会话凭证数据类."""

    token: str = ""
    nas_id: str = ""
    device_id: str = ""
    device: str = "Mac（ZSpace CLI）"
    username: str = ""
    nickname: str = ""
    is_master: int = 0
    nas_name: str = ""
    device_mode: str = ""
    device_pdt: str = ""
    disk_num: int = 0
    local_port: int = DEFAULT_LOCAL_PORT
    lan_ip: str = ""
    login_port: int = DEFAULT_NAS_HTTP_PORT
    host_override: str = ""
    app_version: str = DEFAULT_APP_VERSION
    web_version: str = DEFAULT_WEB_VERSION
    locale: str = "zh_cn"
    sign: str = ""
    cloud_pub_key: str = ""
    nas_pub_key: str = ""
    hostname: str = ""
    platform: str = "mac"
    player_port: int = 0
    mount_volume: str = ""
    vuex_path: Path | None = None
    conf_path: Path | None = None
    cli_session_path: Path | None = None

    # ── 兼容驼峰属性名 (nasId, deviceId, localPort, lanIp 等) ──

    @property
    def nasId(self) -> str:
        """返回 13 位 NAS 设备序列号."""
        return self.nas_id

    @property
    def deviceId(self) -> str:
        """返回 32 位客户端设备 MD5 指纹."""
        return self.device_id

    @property
    def localPort(self) -> int:
        """返回客户端本地代理监听端口（默认 13579）."""
        return self.local_port

    @property
    def lanIp(self) -> str:
        """返回探测或配置得到的 NAS 局域网 IP."""
        return self.lan_ip

    @property
    def nasName(self) -> str:
        """返回 NAS 设备名称."""
        return self.nas_name

    @property
    def deviceMode(self) -> str:
        """返回 NAS 型号（如 z4pro+）."""
        return self.device_mode

    @property
    def playerPort(self) -> int:
        """返回播放器本地 IPC 端口."""
        return self.player_port

    @property
    def proxy_base_url(self) -> str:
        """本地桌面客户端代理 URL."""
        return f"http://127.0.0.1:{self.local_port}"

    @property
    def lan_base_url(self) -> str:
        """局域网直连控制面 URL (默认 :5055)."""
        if not self.lan_ip:
            return ""
        if self.lan_ip.startswith(("http://", "https://")):
            return self.lan_ip.rstrip("/")
        return f"http://{self.lan_ip}:{self.login_port}"

    @property
    def lan_data_url(self) -> str:
        """局域网直连数据面 URL (:8050)，用于文件上传与下载."""
        if not self.lan_ip:
            return ""
        host = self.lan_ip
        if host.startswith(("http://", "https://")):
            host = host.split("://", 1)[1].split(":", 1)[0].split("/", 1)[0]
        return f"http://{host}:{DEFAULT_NAS_DATA_PORT}"

    @property
    def base_url(self) -> str:
        """首选请求网关地址：优先使用环境变量 ZSPACE_HOST，否则使用本地代理."""
        if self.host_override:
            host = self.host_override.strip().rstrip("/")
            if not host.startswith(("http://", "https://")):
                # 若仅传入 IP，默认补齐 http://<ip>:5055
                if ":" not in host:
                    host = f"http://{host}:{self.login_port}"
                else:
                    host = f"http://{host}"
            return host
        return self.proxy_base_url


# 兼容旧版或参考仓库中的 Credentials 名称
Credentials = ZSpaceConfig


def candidate_config_dirs() -> list[Path]:
    """按操作系统返回极空间桌面客户端可能的配置目录列表."""
    home = Path.home()
    if sys.platform == "win32":
        candidates: list[Path] = []
        for env_key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
            val = os.environ.get(env_key)
            if val:
                candidates.append(Path(val) / "zspace")
        if not candidates:
            candidates.append(home / "zspace")
        seen: set[str] = set()
        result: list[Path] = []
        for d in candidates:
            norm = ntpath.normcase(str(d))
            if norm not in seen:
                seen.add(norm)
                result.append(d)
        return result
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "zspace"]
    return [
        home / ".zspace",
        home / ".config" / "zspace",
        home / "zspace",
    ]


def locate_config_dir(config_dir: Path | str | None = None) -> Path | None:
    """定位极空间桌面客户端配置目录."""
    if config_dir:
        p = Path(config_dir).expanduser()
        return p if p.exists() else p

    env_dir = os.environ.get("ZSPACE_CONFIG_DIR") or os.environ.get("ZS_CONFIG_DIR")
    if env_dir:
        return Path(env_dir).expanduser()

    for d in candidate_config_dirs():
        if (d / "vuex.json").exists() or (d / "zspace.conf").exists():
            return d

    # 默认返回第一个候选路径
    dirs = candidate_config_dirs()
    return dirs[0] if dirs else None


def _extract_lan_ip_from_proxy_log(zspace_dir: Path) -> str:
    """从客户端 proxy/log.txt 尾部提取最近一次成功或尝试连接的局域网 NAS IP."""
    for log_name in ("log.txt", "log-backup.txt"):
        log_path = zspace_dir / "proxy" / log_name
        if not log_path.exists():
            continue
        try:
            size = log_path.stat().st_size
            with log_path.open("rb") as fh:
                if size > 65536:
                    fh.seek(size - 65536)
                tail_text = fh.read().decode("utf-8", errors="ignore")
            matches = _LAN_ENDPOINT_RE.findall(tail_text)
            if matches:
                return matches[-1]
        except OSError:
            continue
    return ""


def _read_vuex_json(vuex_path: Path) -> dict[str, Any]:
    """读取并解析 vuex.json（自动兼容明文 JSON 与 AES-256-CBC 加密格式）."""
    raw_bytes = vuex_path.read_bytes()
    text = decrypt_vuex_bytes(raw_bytes)
    return json.loads(text)


def load_config(config_dir: Path | str | None = None) -> ZSpaceConfig:
    """加载完整极空间配置（合并 zspace.conf、vuex.json、CLI 独立会话及环境变量）."""
    cfg = ZSpaceConfig()
    zdir = locate_config_dir(config_dir)

    # 1. 读取 zspace.conf（硬件指纹与播放器端口）
    if zdir:
        conf_file = zdir / "zspace.conf"
        if conf_file.exists():
            cfg.conf_path = conf_file
            try:
                conf_data = json.loads(conf_file.read_text(encoding="utf-8-sig"))
                cfg.device_id = str(conf_data.get("deviceId") or cfg.device_id)
                cfg.hostname = str(conf_data.get("hostname") or "")
                cfg.platform = str(conf_data.get("platform") or "mac")
                cfg.player_port = int(conf_data.get("playerPort") or 0)
            except Exception:
                pass

    # 2. 读取 vuex.json（客户端主状态树）
    if zdir:
        vuex_file = zdir / "vuex.json"
        if vuex_file.exists():
            cfg.vuex_path = vuex_file
            try:
                vuex_data = _read_vuex_json(vuex_file)
                state = vuex_data.get("state", vuex_data)
                app_state = state.get("app") or {}
                nas_state = state.get("nas") or {}
                user_state = state.get("user") or {}
                login_info = user_state.get("loginInfo") or {}
                history_users = user_state.get("historyUsers") or []

                cfg.token = str(user_state.get("token") or cfg.token)
                cfg.username = str(user_state.get("username") or "")
                cfg.is_master = int(user_state.get("isMaster") or 0)
                cfg.mount_volume = str(user_state.get("mountVolume") or "")

                cfg.nas_id = str(
                    nas_state.get("nasId")
                    or login_info.get("nasId")
                    or cfg.nas_id
                )
                cfg.nas_name = str(nas_state.get("nasName") or "")
                cfg.device_mode = str(nas_state.get("deviceMode") or "")
                cfg.device_pdt = str(nas_state.get("devicePdt") or "")
                cfg.disk_num = int(nas_state.get("diskNum") or 0)
                cfg.locale = str(nas_state.get("locale") or "zh_cn")
                cfg.sign = str(nas_state.get("sign") or "")
                cfg.cloud_pub_key = str(nas_state.get("cloudPubKey") or "")
                cfg.nas_pub_key = str(nas_state.get("nasPubKey") or "")

                cfg.device_id = str(app_state.get("deviceId") or cfg.device_id)
                cfg.device = str(app_state.get("device") or cfg.device)
                cfg.app_version = str(app_state.get("version") or DEFAULT_APP_VERSION)
                if app_state.get("localPort"):
                    cfg.local_port = int(app_state["localPort"])

                if login_info.get("loginPort"):
                    cfg.login_port = int(login_info["loginPort"])

                # 提取局域网 IP：先看 nasIp / deviceIp / historyUsers
                nas_ip = nas_state.get("nasIp")
                if isinstance(nas_ip, list) and nas_ip:
                    cfg.lan_ip = str(nas_ip[0])
                elif isinstance(nas_ip, str) and nas_ip:
                    cfg.lan_ip = nas_ip
                elif login_info.get("deviceIp"):
                    cfg.lan_ip = str(login_info["deviceIp"])

                if history_users and isinstance(history_users[0], dict):
                    first_hist = history_users[0]
                    if not cfg.nickname:
                        cfg.nickname = str(first_hist.get("nickname") or "")
                    if not cfg.lan_ip and first_hist.get("ip"):
                        cfg.lan_ip = str(first_hist["ip"])
            except Exception:
                pass

        # 若 vuex.json 中未记录局域网 IP，尝试从 proxy/log.txt 中提取
        if not cfg.lan_ip:
            cfg.lan_ip = _extract_lan_ip_from_proxy_log(zdir)

    # 3. 读取 CLI 独立登录保存的会话文件（~/.config/zspace-cli/session.json）
    if CLI_SESSION_PATH.exists():
        cfg.cli_session_path = CLI_SESSION_PATH
        try:
            sess = json.loads(CLI_SESSION_PATH.read_text(encoding="utf-8"))
            # 当客户端无 token 或 CLI 会话显式设置 override 时覆盖
            if sess.get("override", False) or not cfg.token:
                cfg.token = str(sess.get("token") or cfg.token)
                cfg.nas_id = str(sess.get("nas_id") or cfg.nas_id)
                cfg.username = str(sess.get("username") or cfg.username)
                cfg.nas_name = str(sess.get("nas_name") or cfg.nas_name)
                cfg.lan_ip = str(sess.get("lan_ip") or cfg.lan_ip)
                cfg.host_override = str(sess.get("host") or cfg.host_override)
                cfg.device_id = str(sess.get("device_id") or cfg.device_id)
                cfg.sign = str(sess.get("sign") or cfg.sign)
        except Exception:
            pass

    # 4. 确保 device_id 存在（若无客户端配置则根据本机 MAC 地址生成固定 MD5）
    if not cfg.device_id:
        mac_str = f"{uuid.getnode():012x}"
        cfg.device_id = hashlib.md5(mac_str.encode("utf-8")).hexdigest()

    # 5. 环境变量最高优先级覆盖
    env_token = os.environ.get("ZSPACE_TOKEN") or os.environ.get("ZS_TOKEN")
    if env_token:
        cfg.token = env_token.strip()

    env_nas_id = os.environ.get("ZSPACE_NAS_ID") or os.environ.get("ZS_NAS_ID")
    if env_nas_id:
        cfg.nas_id = env_nas_id.strip()

    env_device_id = os.environ.get("ZSPACE_DEVICE_ID") or os.environ.get("ZS_DEVICE_ID")
    if env_device_id:
        cfg.device_id = env_device_id.strip()

    env_lan_ip = os.environ.get("ZSPACE_LAN_IP") or os.environ.get("ZS_LAN_IP")
    if env_lan_ip:
        cfg.lan_ip = env_lan_ip.strip()

    env_port = os.environ.get("ZSPACE_LOCAL_PORT") or os.environ.get("ZS_LOCAL_PORT")
    if env_port and env_port.isdigit():
        cfg.local_port = int(env_port)

    env_host = (
        os.environ.get("ZSPACE_HOST")
        or os.environ.get("ZSPACE_BASE_URL")
        or os.environ.get("ZS_BASE_URL")
    )
    if env_host:
        cfg.host_override = env_host.strip()

    return cfg


def save_cli_session(
    *,
    token: str,
    nas_id: str = "",
    username: str = "",
    nas_name: str = "",
    lan_ip: str = "",
    host: str = "",
    device_id: str = "",
    sign: str = "",
    override: bool = True,
) -> Path:
    """将 `zs auth login` 获取到的会话凭证持久化到 ~/.config/zspace-cli/session.json."""
    CLI_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "nas_id": nas_id,
        "username": username,
        "nas_name": nas_name,
        "lan_ip": lan_ip,
        "host": host,
        "device_id": device_id,
        "sign": sign,
        "override": override,
    }
    CLI_SESSION_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return CLI_SESSION_PATH


# 兼容函数别名
load_credentials = load_config
get_config = load_config
