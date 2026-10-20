"""极空间内嵌 Portainer API (`/docker/api/*`) 与原生 ZDocker (`/zdocker/*`) 双引擎客户端封装.

极空间 Docker 架构说明（逆向自 Webpack 模块 `33447` 与 `94382`）：
1. **旧版/Portainer 引擎 (`/docker/api/*`, 模块 33447)**：
   - 认证流程：向 `POST /docker/api/auth` 发送 JSON `{"nid": nas_id, "t": token, "uuid": device_id}`，获取 `jwt` 令牌（`dockerToken`）。
   - 请求拦截器：所有发往 `/docker/api/*`（除 `/docker/api/auth` 外）以及 `/settings?create_container` 的请求，均在 HTTP Header 中注入 `Authorization: Bearer <jwt>`（或直接注入返回的 `jwt` 字符串），且不在 Query/Body 中混入极空间网关 `common_params`。
   - 端点路由：通过 `GET /docker/api/endpoints` 获取 `endPointId`（通常为 `1`），随后调用 `/docker/api/endpoints/{endPointId}/docker/...` 标准 Docker Engine API。
2. **新版原生 ZDocker 引擎 (`/zdocker/*`, 模块 94382)**：
   - 极空间 ZOS 新版本（前端 Vuex `docker.isNewVersion = true`）将容器与 Compose 编排能力迁移至原生 `/zdocker/*` Go 服务，此时 `/docker/api/*` 可能返回 `502 Bad Gateway`。
   - 本客户端同时封装了 Portainer `/docker/api/*` 自动鉴权协议与 `/zdocker/*` 自动降级回退能力，确保在新旧固件上均可无缝工作。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from zspace_cli.core.client import ZSpaceClient, ZSpaceError


class PortainerError(ZSpaceError):
    """Portainer / Docker API 调用异常."""


class PortainerClient:
    """极空间 Portainer (`/docker/api/*`) 自动鉴权客户端（支持 `/zdocker/*` 双引擎透明回退）."""

    def __init__(
        self,
        zs_client: Optional[ZSpaceClient] = None,
        *,
        endpoint_id: Optional[int] = None,
        docker_token: Optional[str] = None,
    ) -> None:
        self._owns_client = zs_client is None
        self.zs: ZSpaceClient = zs_client or ZSpaceClient()
        self.docker_token: Optional[str] = docker_token
        self.endpoint_id: Optional[int] = endpoint_id
        self._portainer_available: Optional[bool] = None

    def close(self) -> None:
        """关闭底层 HTTP 客户端."""
        if self._owns_client:
            self.zs.close()

    def __enter__(self) -> "PortainerClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ── Portainer 鉴权与底层请求 ──

    def authenticate(self, force: bool = False) -> str:
        """调用 `POST /docker/api/auth` 获取 Portainer JWT 令牌 (`dockerToken`).

        前端逆向逻辑（模块 `33447` `auth`）：
        请求体为 `application/json`，包含 `nid` (nas_id)、`t` (token 或过期时间戳)、`uuid` (device_id)。
        """
        if self.docker_token and not force:
            return self.docker_token

        creds = self.zs.creds
        auth_payload = {
            "nid": creds.nas_id,
            "t": creds.token,
            "uuid": creds.device_id,
        }
        try:
            resp = self.zs.post(
                "/docker/api/auth",
                json_data=auth_payload,
                request_as_json=True,
                check_code=False,
            )
        except httpx.HTTPStatusError as exc:
            self._portainer_available = False
            raise PortainerError(
                f"Portainer 网关 (/docker/api/auth) 不可用 (HTTP {exc.response.status_code})，"
                "当前固件可能已全面启用原生 /zdocker 引擎",
                data=str(exc),
            ) from exc

        # 提取 jwt 字段（兼容直接返回 {"jwt": "..."} 或 {"data": {"jwt": "..."}}）
        jwt_val = (
            resp.get("jwt")
            or (resp.get("data") or {}).get("jwt")
            if isinstance(resp.get("data"), dict)
            else resp.get("jwt")
        )
        if not jwt_val and isinstance(resp.get("data"), str):
            jwt_val = resp["data"]

        if not jwt_val:
            self._portainer_available = False
            raise PortainerError("未能从 /docker/api/auth 响应中提取 jwt 令牌", data=resp)

        token_str = str(jwt_val).strip()
        if not token_str.lower().startswith("bearer "):
            token_str = f"Bearer {token_str}"

        self.docker_token = token_str
        self._portainer_available = True
        return self.docker_token

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        authenticate_if_needed: bool = True,
    ) -> dict[str, Any]:
        """向 `/docker/api/*` 发起带 `Authorization: <dockerToken>` 头的请求."""
        req_headers = dict(headers or {})
        is_auth_ep = "/docker/api/auth" in endpoint

        if not is_auth_ep and authenticate_if_needed:
            if not self.docker_token:
                self.authenticate()
            if self.docker_token:
                req_headers["Authorization"] = self.docker_token

        return self.zs.request(
            method,
            endpoint,
            params=params,
            json_data=json_data,
            data=data,
            headers=req_headers,
            request_as_json=json_data is not None,
            check_code=False,
        )

    def ensure_endpoint_id(self) -> int:
        """获取并缓存 Portainer `endPointId`（默认回退为 `1`）."""
        if self.endpoint_id is not None:
            return self.endpoint_id

        try:
            resp = self.request("GET", "/docker/api/endpoints")
            # Portainer /docker/api/endpoints 响应可能是列表或包装在 data/raw 中
            endpoints = resp.get("data") if isinstance(resp.get("data"), list) else None
            if endpoints and isinstance(endpoints[0], dict) and "Id" in endpoints[0]:
                self.endpoint_id = int(endpoints[0]["Id"])
                return self.endpoint_id
            if "Id" in resp:
                self.endpoint_id = int(resp["Id"])
                return self.endpoint_id
        except Exception:
            pass

        self.endpoint_id = 1
        return self.endpoint_id

    # ── 高阶方法（支持 Portainer `/docker/api/*` 与原生 `/zdocker/*` 自动回退） ──

    def get_status(self) -> dict[str, Any]:
        """查询 Portainer 状态 (`GET /docker/api/status`)，若 502 则回退至 `GET /zdocker/docker_info`."""
        try:
            return self.request("GET", "/docker/api/status", authenticate_if_needed=False)
        except Exception:
            return self.zs.get("/zdocker/docker_info")

    def docker_info(self) -> dict[str, Any]:
        """获取 Docker 引擎版本与系统信息."""
        if self._portainer_available is False:
            return self.zs.get("/zdocker/docker_info")
        try:
            eid = self.ensure_endpoint_id()
            return self.request("GET", f"/docker/api/endpoints/{eid}/docker/info")
        except Exception:
            self._portainer_available = False
            return self.zs.get("/zdocker/docker_info")

    def list_containers(
        self,
        *,
        search: str = "",
        prefer_zdocker: bool = True,
    ) -> list[dict[str, Any]]:
        """获取容器列表（优先使用极空间 `/zdocker/v2/container_list` 增强字段，或使用 Portainer 端点）."""
        if prefer_zdocker or self._portainer_available is False:
            payload: dict[str, Any] = {}
            if search:
                payload["search"] = search
            resp = self.zs.post(
                "/zdocker/v2/container_list",
                json_data=payload,
                request_as_json=True,
            )
            items = resp.get("data")
            return items if isinstance(items, list) else []

        eid = self.ensure_endpoint_id()
        resp = self.request(
            "GET",
            f"/docker/api/endpoints/{eid}/docker/containers/json",
            params={"all": 1},
        )
        items = resp.get("data") if "data" in resp else resp
        return items if isinstance(items, list) else []

    def inspect_container(
        self,
        container_id: str,
        *,
        prefer_zdocker: bool = True,
    ) -> dict[str, Any]:
        """获取单个容器详细配置 (`GET /zdocker/container_info` 或 Portainer `/containers/{id}/json`)."""
        if prefer_zdocker or self._portainer_available is False:
            resp = self.zs.get("/zdocker/container_info", params={"container_id": container_id})
            data = resp.get("data")
            return data if isinstance(data, dict) else resp

        eid = self.ensure_endpoint_id()
        return self.request("GET", f"/docker/api/endpoints/{eid}/docker/containers/{container_id}/json")

    def container_logs(
        self,
        container_id: str,
        *,
        prefer_zdocker: bool = True,
    ) -> str:
        """获取容器日志 (`GET /zdocker/container_logs` 或 Portainer `/containers/{id}/logs`)."""
        if prefer_zdocker or self._portainer_available is False:
            resp = self.zs.get("/zdocker/container_logs", params={"container_id": container_id})
            data = resp.get("data")
            if isinstance(data, str):
                return data
            return str(resp.get("raw_text") or data or "")

        eid = self.ensure_endpoint_id()
        resp = self.request(
            "GET",
            f"/docker/api/endpoints/{eid}/docker/containers/{container_id}/logs",
            params={"stdout": 1, "stderr": 1, "timestamps": 1, "tail": 200},
        )
        return str(resp.get("raw_text") or resp.get("data") or "")

    def container_top(
        self,
        container_id: str,
        *,
        prefer_zdocker: bool = True,
    ) -> dict[str, Any]:
        """获取容器内部进程列表 (`GET /zdocker/container_top` 或 Portainer `/containers/{id}/top`)."""
        if prefer_zdocker or self._portainer_available is False:
            resp = self.zs.get("/zdocker/container_top", params={"container_id": container_id})
            data = resp.get("data")
            return data if isinstance(data, dict) else resp

        eid = self.ensure_endpoint_id()
        return self.request("GET", f"/docker/api/endpoints/{eid}/docker/containers/{container_id}/top")

    def container_operation(
        self,
        container_id: str,
        action: str,
        *,
        prefer_zdocker: bool = True,
    ) -> dict[str, Any]:
        """控制容器启停状态 (`start` / `stop` / `restart` / `remove` / `force_remove`)."""
        if prefer_zdocker or self._portainer_available is False:
            return self.zs.get(
                "/zdocker/container_op",
                params={"container_id": container_id, "action": action},
            )

        eid = self.ensure_endpoint_id()
        if action in ("remove", "force_remove"):
            return self.request(
                "DELETE",
                f"/docker/api/endpoints/{eid}/docker/containers/{container_id}",
                params={"force": 1, "v": 0},
            )
        return self.request(
            "POST",
            f"/docker/api/endpoints/{eid}/docker/containers/{container_id}/{action}",
        )
