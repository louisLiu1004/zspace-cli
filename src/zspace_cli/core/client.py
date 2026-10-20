"""极空间 (ZSpace NAS) 核心 HTTP 通信客户端 (ZSpaceClient).

实现特性：
1. 强制 httpx.Client(trust_env=False)，彻底规避 macOS 系统代理（Clash/Surge）劫持 127.0.0.1:13579
2. 双通道鉴权自动注入：
   - Header Cookie: token, zenithtoken, nas_id, nasid, device_id, plat, version, _l
   - 公共业务参数: token, nasid, plat="web", version="2.3.2026042401", device_id, _l="zh_cn"
3. 多编码格式支持：
   - application/x-www-form-urlencoded（支持数组参数自动编码为 key[]=val）
   - application/json（request_as_json=True，不混入表单公共参数，仅通过 Cookie 鉴权）
   - multipart/form-data（用于种子上传等文件表单接口）
   - application/octet-stream（用于文件流上传）
4. 本地代理 (http://127.0.0.1:13579) 与局域网 IP 直连 (http://<lan_ip>:5055) 自动回退与切换
5. 支持 remote_port=8050 数据传输端口路由
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from zspace_cli.core.config import (
    CLOUD_API_BASE,
    DEFAULT_NAS_DATA_PORT,
    DEFAULT_WEB_VERSION,
    SHARE_API_BASE,
    ZSpaceConfig,
    load_config,
    save_cli_session,
)
from zspace_cli.core.crypto import rsa_encrypt


def _urlencode(val: Any) -> str:
    """将参数值做标准 URL 百分号编码（保留安全字符语义与前端 encodeURIComponent 对齐）."""
    return urllib.parse.quote(str(val), safe="")


def encode_form_body(data: dict[str, Any]) -> str:
    """将字典序列化为 x-www-form-urlencoded 字符串，列表/元组自动展开为 key[]=val."""
    parts: list[str] = []
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            arr_key = k if k.endswith("[]") else f"{k}[]"
            encoded_key = _urlencode(arr_key)
            for item in v:
                if isinstance(item, (dict, list)):
                    parts.append(f"{encoded_key}={_urlencode(json.dumps(item, ensure_ascii=False))}")
                else:
                    parts.append(f"{encoded_key}={_urlencode(item)}")
        elif isinstance(v, bool):
            parts.append(f"{_urlencode(k)}={'true' if v else 'false'}")
        elif isinstance(v, dict):
            parts.append(f"{_urlencode(k)}={_urlencode(json.dumps(v, ensure_ascii=False))}")
        else:
            parts.append(f"{_urlencode(k)}={_urlencode(v)}")
    return "&".join(parts)


class ZSpaceError(Exception):
    """极空间 API 业务层或协议层异常."""

    def __init__(
        self,
        code: str | int,
        msg: str,
        endpoint: str = "",
        payload: dict[str, Any] | None = None,
    ):
        self.code = str(code)
        self.msg = msg
        self.endpoint = endpoint
        self.payload = payload or {}
        prefix = f"[{self.code}]"
        if endpoint:
            prefix += f" ({endpoint})"
        super().__init__(f"{prefix} {msg}")

    @staticmethod
    def diagnose(code: str, msg: str = "") -> str | None:
        """为常见错误码提供中文排障建议."""
        low = msg.lower()
        if code in ("401", "403", "N001102", "N001103") or "登录" in msg or "token" in low:
            return "登录凭证已失效或未授权 — 请确认极空间客户端已登录，或执行 `zs auth login`"
        if code in ("N001411",) or "无权限" in msg or "permission" in low:
            return "没有访问权限 — 请检查当前账号对目标路径或模块的访问权限"
        if code in ("404", "N001315") or "不存在" in msg or "not found" in low:
            return "目标资源或路径不存在 — 请检查路径或参数是否正确"
        if code in ("N001414", "N001204"):
            return "触发新设备登录二次验证（短信/邮箱）— 建议先在极空间桌面客户端完成一次登录"
        return None


@dataclass
class EndpointProbeResult:
    """网关连接探测结果."""

    name: str
    url: str
    reachable: bool
    latency_ms: float = 0.0
    status_code: int = 0
    detail: str = ""


class ZSpaceClient:
    """极空间全量 API 核心通信客户端."""

    DEFAULT_API_VERSION = DEFAULT_WEB_VERSION

    def __init__(
        self,
        base_url: str | None = None,
        config: ZSpaceConfig | None = None,
        config_dir: Path | str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_delay: float = 0.25,
        auto_fallback_lan: bool = True,
    ):
        self.config: ZSpaceConfig = config or load_config(config_dir)
        # 兼容 _creds / creds 属性访问
        self._creds = self.config
        self.creds = self.config

        self.api_version = api_version or self.config.web_version
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.auto_fallback_lan = auto_fallback_lan

        self._explicit_base_url = base_url.rstrip("/") if base_url else ""
        self.base_url = self._explicit_base_url or self.config.base_url
        self._using_lan_direct = False
        if self.config.lan_ip and self.config.lan_ip in self.base_url:
            self._using_lan_direct = True

        # 核心要求：务必设置 trust_env=False 避免被本机系统代理（如 Clash）拦截
        self._http = httpx.Client(
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=True,
        )

    def close(self) -> None:
        """关闭底层 HTTP 连接池."""
        self._http.close()

    def __enter__(self) -> ZSpaceClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── 鉴权与请求构造辅助 ──

    def _build_cookie_header(self, extra_cookies: dict[str, str] | None = None) -> str:
        """构造双通道鉴权所需的完整 Cookie Header 字符串."""
        cookies: dict[str, str] = {
            "token": _urlencode(self.config.token),
            "zenithtoken": _urlencode(self.config.token),
            "nas_id": self.config.nas_id,
            "nasid": self.config.nas_id,
            "device_id": self.config.device_id,
            "plat": "web",
            "version": self.api_version,
            "_l": self.config.locale or "zh_cn",
        }
        if self.config.device:
            cookies["device"] = _urlencode(self.config.device)
        if extra_cookies:
            cookies.update(extra_cookies)
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    def common_params(self, endpoint: str = "") -> dict[str, str]:
        """构造极空间标准公共请求参数."""
        params: dict[str, str] = {
            "token": self.config.token,
            "nasid": self.config.nas_id,
            "plat": "web",
            "version": self.api_version,
            "device_id": self.config.device_id,
            "_l": self.config.locale or "zh_cn",
        }
        if self.config.device:
            params["device"] = self.config.device
        # 极相册模块接口自动注入 app="album"
        if endpoint.startswith("/v2/album/"):
            params["app"] = "album"
        return params

    # 兼容下划线调用
    _common_params = common_params

    @staticmethod
    def _make_rnd() -> str:
        """生成防缓存随机后缀 rnd."""
        ms_ts = int(time.time() * 1000)
        rand4 = random.randint(1000, 9999)
        return f"{ms_ts}_{rand4}"

    def _resolve_target_url(
        self,
        endpoint: str,
        api_target: str = "",
        remote_port: int | None = None,
        use_lan: bool | None = None,
    ) -> str:
        """根据 endpoint、api_target、remote_port 解析最终完整请求 URL."""
        clean_ep = endpoint.strip()
        # 清理逆向提取可能残留的模板变量如 {{d}}
        if clean_ep.startswith("{{") and "}}" in clean_ep:
            clean_ep = clean_ep.split("}}", 1)[1]

        # 若已经是完整 http/https URL
        if clean_ep.startswith(("http://", "https://")):
            return clean_ep

        if not clean_ep.startswith("/"):
            clean_ep = "/" + clean_ep

        # 判断网关目标
        if api_target in ("server", "email"):
            gateway = CLOUD_API_BASE
        elif api_target == "share":
            gateway = SHARE_API_BASE
        else:
            is_lan = self._using_lan_direct if use_lan is None else use_lan
            if is_lan and self.config.lan_base_url:
                if remote_port == DEFAULT_NAS_DATA_PORT:
                    gateway = self.config.lan_data_url
                else:
                    gateway = self.config.lan_base_url
            else:
                gateway = self.base_url

        full_url = f"{gateway.rstrip('/')}{clean_ep}"

        # 若通过本地代理访问指定远端端口（如 8050），在 Query 中附带 remote_port
        if remote_port and not (use_lan or self._using_lan_direct):
            sep = "&" if "?" in full_url else "?"
            full_url = f"{full_url}{sep}remote_port={remote_port}"

        return full_url

    def _append_rnd_to_url(self, url: str) -> str:
        """在 URL 末尾追加 ?&rnd=...&webagent=v2."""
        rnd = self._make_rnd()
        if "?" in url:
            return f"{url}&rnd={rnd}&webagent=v2"
        return f"{url}?&rnd={rnd}&webagent=v2"

    # ── 连通性探测与自动回退 ──

    def probe_endpoint(self, url: str, name: str = "", timeout: float = 3.0) -> EndpointProbeResult:
        """探测指定网关地址（本地代理或局域网 IP）是否可达."""
        target = f"{url.rstrip('/')}/key/pub?signme=1"
        t0 = time.perf_counter()
        try:
            resp = self._http.get(target, timeout=timeout)
            cost_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code < 500:
                detail = f"HTTP {resp.status_code}"
                try:
                    data = resp.json().get("data") or {}
                    if data.get("name"):
                        detail += f" (NAS: {data['name']})"
                except Exception:
                    pass
                return EndpointProbeResult(
                    name=name or url,
                    url=url,
                    reachable=True,
                    latency_ms=cost_ms,
                    status_code=resp.status_code,
                    detail=detail,
                )
            return EndpointProbeResult(
                name=name or url,
                url=url,
                reachable=False,
                latency_ms=cost_ms,
                status_code=resp.status_code,
                detail=f"返回异常状态码 HTTP {resp.status_code}",
            )
        except httpx.ConnectError:
            return EndpointProbeResult(
                name=name or url,
                url=url,
                reachable=False,
                detail="连接被拒绝（服务未启动或不可达）",
            )
        except httpx.TimeoutException:
            return EndpointProbeResult(
                name=name or url,
                url=url,
                reachable=False,
                detail="连接超时",
            )
        except Exception as exc:
            return EndpointProbeResult(
                name=name or url,
                url=url,
                reachable=False,
                detail=str(exc),
            )

    def check_connectivity(self) -> dict[str, EndpointProbeResult]:
        """同时探测桌面端本地代理与局域网直连状态."""
        results: dict[str, EndpointProbeResult] = {}
        proxy_url = self.config.proxy_base_url
        results["proxy"] = self.probe_endpoint(proxy_url, name="本地代理 (libzrpc)")

        if self.config.lan_base_url:
            results["lan"] = self.probe_endpoint(
                self.config.lan_base_url,
                name="局域网直连 (LAN)",
            )
        return results

    # ── 统一请求发送入口 ──

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_data: Any = None,
        files: Any = None,
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        request_as_json: bool = False,
        api_target: str = "",
        remote_port: int | None = None,
        append_common: bool = True,
        check_code: bool = True,
        raw_response: bool = False,
        **kwargs: Any,
    ) -> Any:
        """发送极空间 API 请求（自动处理鉴权、序列化、防重放尾缀与局域网回退）.

        Args:
            method: HTTP 方法 ('GET', 'POST', 'PUT', 'DELETE' 等)
            endpoint: API 路由路径（如 '/zspool/info'）
            params: URL Query 参数字典
            data: 表单或业务参数字典
            json_data: JSON 请求体（若提供则自动启用 application/json）
            files: multipart/form-data 上传文件字典
            content: 原始字节流或字符串请求体
            headers: 自定义请求头
            request_as_json: 是否以 application/json 方式序列化请求体
            api_target: 网关类型 ('', 'direct', 'server', 'email', 'share')
            remote_port: 远端目标端口（如文件传输端口 8050）
            append_common: 是否注入 token/nasid/device_id 等公共参数
            check_code: 是否检查响应 code == "200" 并在失败时抛出 ZSpaceError
            raw_response: 若为 True，直接返回 httpx.Response 对象
        """
        # 兼容调用方传入 json=... 参数
        if "json" in kwargs and json_data is None:
            json_data = kwargs.pop("json")

        http_method = method.upper()
        if http_method == "REMOVE":
            http_method = "DELETE"

        is_docker_api = "/docker/api" in endpoint
        if json_data is not None:
            request_as_json = True

        # 构造 Header（含双通道 Cookie 鉴权）
        req_headers: dict[str, str] = {
            "Cookie": self._build_cookie_header(),
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) zspace/2.45.2026082601"
            ),
        }
        if remote_port:
            req_headers["remote-port"] = str(remote_port)
        if headers:
            req_headers.update(headers)

        # 构造 Query 与 Body
        query_params: dict[str, Any] = {}
        if params:
            query_params.update(params)

        body_content: bytes | str | None = content
        multipart_data: dict[str, Any] | None = None

        if http_method == "GET":
            if append_common and not is_docker_api and not request_as_json:
                merged_q = self.common_params(endpoint)
                if data:
                    merged_q.update(data)
                merged_q.update(query_params)
                query_params = merged_q
            elif data:
                query_params.update(data)
            query_params["rnd"] = self._make_rnd()
            query_params["webagent"] = "v2"
        else:
            # POST / PUT / DELETE
            if files is not None:
                # multipart/form-data 模式
                form_fields: dict[str, Any] = {}
                if append_common and not is_docker_api:
                    form_fields.update(self.common_params(endpoint))
                if data:
                    form_fields.update(data)
                multipart_data = form_fields
            elif request_as_json:
                # application/json 模式：不在 JSON Body 内混入表单公共参数，鉴权走 Cookie
                req_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
                payload_obj = json_data if json_data is not None else (data or {})
                body_content = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
            elif content is None:
                # 默认 application/x-www-form-urlencoded 模式
                req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
                form_dict: dict[str, Any] = {}
                if append_common and not is_docker_api:
                    form_dict.update(self.common_params(endpoint))
                if data:
                    form_dict.update(data)
                body_content = encode_form_body(form_dict)

        return self._send_with_fallback(
            method=http_method,
            endpoint=endpoint,
            api_target=api_target,
            remote_port=remote_port,
            params=query_params if query_params else None,
            content=body_content,
            data=multipart_data,
            files=files,
            headers=req_headers,
            check_code=check_code,
            raw_response=raw_response,
        )

    def _send_with_fallback(
        self,
        *,
        method: str,
        endpoint: str,
        api_target: str,
        remote_port: int | None,
        params: dict[str, Any] | None,
        content: bytes | str | None,
        data: dict[str, Any] | None,
        files: Any,
        headers: dict[str, str],
        check_code: bool,
        raw_response: bool,
    ) -> Any:
        """执行 HTTP 请求，支持指数退避重试与本地代理失败时自动回退至局域网直连."""
        for attempt in range(self.max_retries + 1):
            url = self._resolve_target_url(
                endpoint,
                api_target=api_target,
                remote_port=remote_port,
            )
            if method != "GET":
                url = self._append_rnd_to_url(url)

            try:
                resp = self._http.request(
                    method=method,
                    url=url,
                    params=params,
                    content=content,
                    data=data,
                    files=files,
                    headers=headers,
                )
                if raw_response:
                    return resp
                return self._parse_and_validate(resp, endpoint=endpoint, check_code=check_code)
            except ZSpaceError:
                raise
            except httpx.ConnectError:
                # 若当前走的是本地代理 127.0.0.1:13579 且配置中有局域网 IP，自动切换到局域网直连重试
                if (
                    self.auto_fallback_lan
                    and not self._using_lan_direct
                    and not self._explicit_base_url
                    and self.config.lan_base_url
                    and api_target in ("", "direct")
                ):
                    self._using_lan_direct = True
                    self.base_url = self.config.lan_base_url
                    continue
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(self.retry_delay * (2**attempt), 2.0))
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status < 500 and status != 429:
                        raise
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(self.retry_delay * (2**attempt), 2.0))

        raise RuntimeError(f"请求失败: {method} {endpoint}")

    @staticmethod
    def _parse_and_validate(
        resp: httpx.Response,
        endpoint: str = "",
        check_code: bool = True,
    ) -> Any:
        """解析响应 JSON 并根据 check_code 校验业务状态码."""
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text
        if not text.strip():
            return {}

        try:
            body = resp.json()
        except ValueError:
            if "application/json" not in content_type:
                return {"raw_text": text, "status_code": resp.status_code}
            raise

        if not check_code or not isinstance(body, dict):
            return body

        # 兼容极空间各模块返回格式：
        # 1. 标准 Web 接口: {"code": "200", "msg": "success", "data": ...} 或 {"code": 200, ...}
        # 2. 新版微服务接口: {"code": 0, "message": "ok", "data": ...} 或 {"errcode": 0, ...}
        # 3. 迅雷/部分插件接口: {"errno": 0, ...} 或无 code 字段直接返回业务 JSON
        if "code" in body:
            code_str = str(body.get("code"))
            if code_str not in ("200", "0", "None"):
                msg = str(
                    body.get("msg")
                    or body.get("message")
                    or body.get("error")
                    or "未知业务错误"
                )
                raise ZSpaceError(code_str, msg, endpoint=endpoint, payload=body)
        elif "errcode" in body:
            err_str = str(body.get("errcode"))
            if err_str not in ("0", "200", "None"):
                msg = str(body.get("errmsg") or body.get("msg") or "未知业务错误")
                raise ZSpaceError(err_str, msg, endpoint=endpoint, payload=body)

        return body

    # ── 便捷 HTTP 方法封装 ──

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """发送 GET 请求."""
        return self.request("GET", endpoint, params=params, **kwargs)

    def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        request_as_json: bool = False,
        **kwargs: Any,
    ) -> Any:
        """发送 POST 请求（默认 form-urlencoded，支持 request_as_json=True）."""
        return self.request(
            "POST",
            endpoint,
            params=params,
            data=data,
            json_data=json_data,
            request_as_json=request_as_json,
            **kwargs,
        )

    def post_form(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """发送 application/x-www-form-urlencoded POST 请求."""
        return self.request("POST", endpoint, data=data, request_as_json=False, **kwargs)

    def post_json(
        self,
        endpoint: str,
        json_data: Any = None,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """发送 application/json POST 请求."""
        payload = json_data if json_data is not None else (data or {})
        return self.request(
            "POST",
            endpoint,
            json_data=payload,
            request_as_json=True,
            **kwargs,
        )

    def post_multipart(
        self,
        endpoint: str,
        files: Any,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """发送 multipart/form-data POST 请求（如上传种子文件）."""
        return self.request("POST", endpoint, data=data, files=files, **kwargs)

    # 兼容参考实现的方法签名
    def _post(self, endpoint: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post(endpoint, data=extra)

    def _post_with_array(
        self,
        endpoint: str,
        paths: list[str],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = dict(extra or {})
        payload["paths[]"] = list(paths)
        return self.post(endpoint, data=payload)

    # ── 常用高层 API 方法（供 auth / status / api 等命令使用） ──

    def call_by_catalog(
        self,
        url_or_fn: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        method_override: str | None = None,
        check_code: bool = False,
    ) -> tuple[Any, Any]:
        """根据内置 2,004 接口目录元数据直接发起请求."""
        from zspace_cli.core.catalog import get_catalog

        catalog = get_catalog()
        return catalog.call_by_catalog(
            self,
            url_or_fn=url_or_fn,
            params=params,
            json_body=json_body,
            method_override=method_override,
            check_code=check_code,
        )

    def pool_info(self) -> dict[str, Any]:
        """获取存储池与磁盘详情 (`POST /zspool/info`)."""
        return self.post("/zspool/info")

    def disk_usage(self) -> dict[str, Any]:
        """获取磁盘空间分类占用详情 (`POST /system/diskusage3`)."""
        return self.post("/system/diskusage3")

    def disk_usage_status(self) -> dict[str, Any]:
        """获取磁盘空间统计任务状态 (`POST /system/diskusage/status`)."""
        return self.post("/system/diskusage/status")

    def get_public_key(self, host_url: str | None = None) -> dict[str, Any]:
        """调用 `GET /key/pub?signme=1` 获取 NAS 设备 RSA 公钥、设备名与签名."""
        target_base = (host_url or self.base_url).rstrip("/")
        url = f"{target_base}/key/pub?signme=1"
        resp = self._http.get(url, timeout=10.0)
        body = self._parse_and_validate(resp, endpoint="/key/pub", check_code=True)
        return body.get("data") or body

    def login_with_password(
        self,
        username: str,
        password: str,
        host: str | None = None,
        sms_proof: str = "",
        email_proof: str = "",
        save: bool = True,
    ) -> dict[str, Any]:
        """通过局域网 IP（或本地代理）+ 账号密码执行 RSA 加密独立登录 (`/auth/login`).

        流程：
        1. 请求 `GET /key/pub?signme=1` 获取 2048-bit `nasPubKey`、设备名 `name`、设备签名 `sign`
        2. 使用 RSAES-PKCS1-v1_5 加密去除首尾空白后的账号与密码
        3. 请求 `POST /auth/login` 获取会话 `token`
        4. 可选将新会话保存至 `~/.config/zspace-cli/session.json`
        """
        target_host = (host or self.config.lan_base_url or self.base_url).strip().rstrip("/")
        if not target_host.startswith(("http://", "https://")):
            if ":" not in target_host:
                target_host = f"http://{target_host}:5055"
            else:
                target_host = f"http://{target_host}"

        pub_info = self.get_public_key(target_host)
        pub_key = str(pub_info.get("key") or self.config.nas_pub_key or "")
        if not pub_key:
            raise ZSpaceError("NO_PUBKEY", "未能从目标设备获取到 RSA 公钥 (/key/pub)")

        nas_name = str(pub_info.get("name") or self.config.nas_name or "")
        nas_sign = str(pub_info.get("sign") or self.config.sign or "")
        nas_id = str(pub_info.get("nasid") or pub_info.get("nas_id") or self.config.nas_id or "")

        enc_user = rsa_encrypt(username.strip(), pub_key)
        enc_pwd = rsa_encrypt(password.strip(), pub_key)

        login_form: dict[str, Any] = {
            "username": enc_user,
            "password": enc_pwd,
            "device_id": self.config.device_id,
            "device": self.config.device,
            "plat": "web",
            "version": self.api_version,
            "_l": self.config.locale or "zh_cn",
        }
        if nas_id:
            login_form["nasid"] = nas_id
        if sms_proof:
            login_form["sms_proof"] = sms_proof
        if email_proof:
            login_form["email_proof"] = email_proof

        login_url = self._append_rnd_to_url(f"{target_host}/auth/login")
        resp = self._http.post(
            login_url,
            content=encode_form_body(login_form),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        body = self._parse_and_validate(resp, endpoint="/auth/login", check_code=True)
        data = body.get("data") or body
        token = str(data.get("token") or "")
        if not token:
            raise ZSpaceError("NO_TOKEN", "登录响应成功但未返回 token", endpoint="/auth/login", payload=body)

        resolved_nas_id = str(data.get("nasid") or data.get("nas_id") or nas_id)
        # 提取纯 IP
        lan_ip = ""
        host_part = target_host.split("://", 1)[-1].split(":", 1)[0]
        if host_part not in ("127.0.0.1", "localhost"):
            lan_ip = host_part

        self.config.token = token
        if resolved_nas_id:
            self.config.nas_id = resolved_nas_id
        if nas_name:
            self.config.nas_name = nas_name
        if nas_sign:
            self.config.sign = nas_sign
        self.config.username = username.strip()

        if save:
            save_cli_session(
                token=token,
                nas_id=resolved_nas_id,
                username=username.strip(),
                nas_name=nas_name,
                lan_ip=lan_ip or self.config.lan_ip,
                host=target_host if lan_ip else "",
                device_id=self.config.device_id,
                sign=nas_sign,
                override=True,
            )

        return {
            "token": token,
            "nas_id": resolved_nas_id,
            "nas_name": nas_name,
            "username": username.strip(),
            "host": target_host,
            "raw": data,
        }
