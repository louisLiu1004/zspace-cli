"""极空间内嵌迅雷客户端协议封装模块 (`XunleiClient`).

逆向自极空间 macOS 客户端与内嵌迅雷 Web 前端 (`xunlei_index.js` & `index-1c94ee0b.js`)：
1. 调用极空间 `GET /thunder/getWebUrl` 获取 `weburl`（如 `/xunlei/?device_space=<device_space>`）并提取 `device_space`。
2. 请求 `GET http://127.0.0.1:13579/xunlei/?device_space=<device_space>`，从返回的 HTML 中正则提取
   `function uiauth(value){ return "<JWT>" }` 得到 `pan-auth` Token。
3. 请求 `GET drive/v1/tasks?type=user%23runner` 获取当前迅雷 Runner 任务及其 `params.target`
   （如 `device_id#<32位hex指纹>`），作为后续接口的 `space` 参数。
4. 封装迅雷内部请求方法 `request(path, method="GET", data=None, params=None)`：
   - 基础路径：`http://127.0.0.1:13579/xunlei/{path}`
   - 遵循迅雷前端 `DVe` 改写规则：当方法为 `PATCH` 或 `DELETE` 时，将 URL 改写为
     `method/patch/{path}` 或 `method/delete/{path}` 并使用 **`POST`** 方法发送！
   - 请求头注入：`Cookie`（极空间 Cookie）、`pan-auth: <JWT>`、`Device-Space: <device_space>`、`Content-Type: application/json`。
   - Query 参数自动附带 `pan_auth=<JWT>&device_space=<device_space>`。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from zspace_cli.core.client import ZSpaceClient, ZSpaceError
from zspace_cli.core.config import ZSpaceConfig, load_config

# 从迅雷入口 HTML 中提取 uiauth 返回的 JWT Token
_UIAUTH_RE = re.compile(
    r"""function\s+uiauth\s*\([^)]*\)\s*\{\s*return\s+["']([^"']+)["']""",
    re.IGNORECASE,
)
_DEVICE_SPACE_RE = re.compile(r"device_space=([^&#\s]+)")

# 迅雷任务阶段常量（对齐 xunlei_index.js）
PHASE_PENDING = "PHASE_TYPE_PENDING"
PHASE_RUNNING = "PHASE_TYPE_RUNNING"
PHASE_PAUSED = "PHASE_TYPE_PAUSED"
PHASE_ERROR = "PHASE_TYPE_ERROR"
PHASE_COMPLETE = "PHASE_TYPE_COMPLETE"

PHASES_RUNNING = [PHASE_PENDING, PHASE_RUNNING, PHASE_PAUSED, PHASE_ERROR]
PHASES_COMPLETED = [PHASE_COMPLETE]
PHASES_ALL = [PHASE_PENDING, PHASE_RUNNING, PHASE_PAUSED, PHASE_ERROR, PHASE_COMPLETE]

TASK_TYPE_URL = "user#download-url"
TASK_TYPE_DOWNLOAD = "user#download"
TASK_TYPE_RUNNER = "user#runner"
DEFAULT_TASK_TYPES = f"{TASK_TYPE_URL},{TASK_TYPE_DOWNLOAD}"


class XunleiError(ZSpaceError):
    """极空间内嵌迅雷客户端异常."""


def simplify_xunlei_path(real_path: str) -> str:
    """将迅雷内部挂载真实路径（如 `/tmp/zfsv3/sata11/<uid>/data/迅雷下载/...`）简化为易读路径."""
    if not real_path:
        return ""
    idx = real_path.find("/data/")
    if idx != -1:
        return real_path[idx:]
    if real_path.endswith("/data"):
        return "/data"
    return real_path


def flatten_resources(resources: list[dict[str, Any]], prefix: str = "") -> list[dict[str, Any]]:
    """递归展平 `drive/v1/resource/list` 返回的树形资源结构，提取每个子文件的索引、大小和路径."""
    items: list[dict[str, Any]] = []
    for res in resources or []:
        name = str(res.get("name") or "")
        full_name = f"{prefix}/{name}" if prefix else name
        dir_obj = res.get("dir")
        sub_resources = (dir_obj.get("resources") if isinstance(dir_obj, dict) else None) or []
        is_dir = bool(res.get("is_dir")) or bool(sub_resources)

        if sub_resources:
            items.extend(flatten_resources(sub_resources, prefix=full_name))
        else:
            meta = res.get("meta") or {}
            items.append(
                {
                    "id": str(res.get("id") or ""),
                    "name": full_name,
                    "raw_name": name,
                    "file_index": res.get("file_index", 0),
                    "file_size": int(res.get("file_size") or 0),
                    "file_count": int(res.get("file_count") or 1),
                    "is_dir": is_dir,
                    "mime_type": str(res.get("mime_type") or meta.get("mime_type") or ""),
                    "hash": str(meta.get("hash") or ""),
                    "url": str(meta.get("url") or ""),
                }
            )
    return items


class XunleiClient:
    """极空间内嵌迅雷全功能协议客户端."""

    def __init__(
        self,
        zspace_client: ZSpaceClient | None = None,
        config: ZSpaceConfig | None = None,
    ) -> None:
        self._owns_zspace = zspace_client is None
        self.zspace: ZSpaceClient = zspace_client or ZSpaceClient(config=config or load_config())

        self.web_url: str = ""
        self.device_space: str = ""
        self.pan_auth: str = ""
        self.space: str = ""
        self.runner_task: dict[str, Any] = {}

    def close(self) -> None:
        """若内部自建了 ZSpaceClient，关闭其底层连接."""
        if self._owns_zspace:
            self.zspace.close()

    def __enter__(self) -> XunleiClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── 1. 认证握手与协议底层请求 ──

    def ensure_auth(self, force: bool = False) -> None:
        """完成迅雷三步鉴权握手：获取 `device_space` -> 提取 `pan-auth` JWT -> 获取设备 `space` (`target`)."""
        if self.device_space and self.pan_auth and self.space and not force:
            return

        # 步骤 1：调用极空间 GET /thunder/getWebUrl 获取 weburl 与 device_space
        resp_body = self.zspace.get("/thunder/getWebUrl")
        data = (resp_body.get("data") if isinstance(resp_body, dict) else None) or {}
        weburl = str(data.get("weburl") or "")
        if not weburl:
            raise XunleiError(
                "XUNLEI_NO_WEBURL",
                "未能从 /thunder/getWebUrl 获取迅雷入口地址，请确认极空间迅雷服务已启动",
                endpoint="/thunder/getWebUrl",
                payload=resp_body if isinstance(resp_body, dict) else {},
            )

        self.web_url = weburl
        m_space = _DEVICE_SPACE_RE.search(weburl)
        if not m_space:
            raise XunleiError(
                "XUNLEI_NO_DEVICE_SPACE",
                f"无法从迅雷 weburl ({weburl}) 中解析 device_space",
                endpoint="/thunder/getWebUrl",
            )
        self.device_space = m_space.group(1)

        # 步骤 2：请求 /xunlei/?device_space=<device_space> 提取 uiauth JWT
        html_resp = self.zspace.request(
            "GET",
            weburl,
            append_common=False,
            check_code=False,
            raw_response=True,
        )
        if html_resp.status_code >= 400:
            raise XunleiError(
                str(html_resp.status_code),
                f"访问迅雷面板 {weburl} 失败 (HTTP {html_resp.status_code})",
                endpoint=weburl,
            )

        html_text = html_resp.text
        m_jwt = _UIAUTH_RE.search(html_text)
        if not m_jwt:
            raise XunleiError(
                "XUNLEI_NO_PAN_AUTH",
                "未能在迅雷面板 HTML 中提取到 uiauth JWT Token",
                endpoint=weburl,
            )
        self.pan_auth = m_jwt.group(1)

        # 步骤 3：查询 user#runner 任务获取 target (即设备 space 标识)
        runner_res = self.request(
            "drive/v1/tasks",
            method="GET",
            params={"type": TASK_TYPE_RUNNER, "device_space": self.device_space},
            _skip_ensure=True,
        )
        tasks = (runner_res.get("tasks") if isinstance(runner_res, dict) else None) or []
        if tasks and isinstance(tasks[0], dict):
            self.runner_task = tasks[0]
            params_obj = tasks[0].get("params") or {}
            self.space = str(params_obj.get("target") or tasks[0].get("space") or "")

        # 若 runner 任务未返回 target，尝试通过 device/info/watch 获取
        if not self.space:
            watch_res = self.request(
                "device/info/watch",
                method="POST",
                data={},
                _skip_ensure=True,
            )
            if isinstance(watch_res, dict) and watch_res.get("target"):
                self.space = str(watch_res["target"])

    @staticmethod
    def _apply_dve_rule(path: str, method: str) -> tuple[str, str]:
        """实现迅雷前端 `DVe` 请求改写规则.

        当 HTTP 方法为 `PATCH` 或 `DELETE` 时，迅雷前端将 URL 改写为：
        `method/patch/{path}` 或 `method/delete/{path}`，并统一使用 `POST` 方法发送。
        """
        clean_path = path.lstrip("/")
        m_lower = method.lower()
        if m_lower in ("patch", "delete"):
            return f"method/{m_lower}/{clean_path}", "POST"
        return clean_path, method.upper()

    def request(
        self,
        path: str,
        method: str = "GET",
        data: Any = None,
        params: dict[str, Any] | None = None,
        *,
        _skip_ensure: bool = False,
    ) -> Any:
        """发送迅雷内部 API 请求 (`http://127.0.0.1:13579/xunlei/{path}`)."""
        if not _skip_ensure:
            self.ensure_auth()

        rewritten_path, actual_method = self._apply_dve_rule(path, method)

        # 拆分 path 中可能内嵌的 query 参数（如 deleteTasks 中的 drive/v1/tasks?space=...&task_ids=...）
        if "?" in rewritten_path:
            base_part, inline_query = rewritten_path.split("?", 1)
        else:
            base_part, inline_query = rewritten_path, ""

        # 构造 Query 参数：自动注入 pan_auth 与 device_space
        query_items: list[tuple[str, str]] = []
        if inline_query:
            query_items.extend(urllib.parse.parse_qsl(inline_query, keep_blank_values=True))

        merged_params: dict[str, Any] = {
            "pan_auth": self.pan_auth,
            "device_space": self.device_space,
        }
        if params:
            merged_params.update(params)

        existing_keys = {k for k, _ in query_items}
        for k, v in merged_params.items():
            if v is None:
                continue
            if k in existing_keys and k in ("pan_auth", "device_space"):
                continue
            if isinstance(v, (list, tuple)):
                for item in v:
                    query_items.append((k, str(item)))
            else:
                query_items.append((k, str(v)))

        query_string = urllib.parse.urlencode(query_items, doseq=True)
        full_url = f"{self.zspace.base_url.rstrip('/')}/xunlei/{base_part}"
        if query_string:
            full_url = f"{full_url}?{query_string}"

        headers: dict[str, str] = {
            "Cookie": self.zspace._build_cookie_header(),
            "pan-auth": self.pan_auth,
            "Device-Space": self.device_space,
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) zspace/2.45.2026082601"
            ),
        }

        body_bytes: bytes | None = None
        if actual_method not in ("GET", "HEAD"):
            payload = {} if data is None else data
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        resp = self.zspace._http.request(
            method=actual_method,
            url=full_url,
            content=body_bytes,
            headers=headers,
        )

        text = resp.text.strip()
        if not text:
            if resp.status_code >= 400:
                raise XunleiError(str(resp.status_code), f"迅雷接口返回 HTTP {resp.status_code}", endpoint=path)
            return {}

        try:
            res_json = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise XunleiError(str(resp.status_code), text[:200], endpoint=path)
            return {"raw_text": text}

        if isinstance(res_json, dict) and (resp.status_code >= 400 or res_json.get("error")):
            err_code = str(res_json.get("error") or res_json.get("error_code") or resp.status_code)
            err_desc = str(
                res_json.get("error_description")
                or res_json.get("message")
                or res_json.get("error")
                or f"HTTP {resp.status_code}"
            )
            raise XunleiError(err_code, err_desc, endpoint=path, payload=res_json)

        return res_json

    # ── 2. 任务查询、解析、创建、暂停、恢复与删除 ──

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 20,
        page_token: str = "",
    ) -> list[dict[str, Any]]:
        """查询迅雷任务列表 (`GET drive/v1/tasks`).

        Args:
            status: 过滤状态，支持:
                - `'running'` / `'ing'`: 进行中/暂停/等待/异常任务
                - `'completed'` / `'done'`: 已完成任务
                - `None` / `'all'`: 全部任务
            limit: 返回条数上限（默认 20）
            page_token: 分页游标
        """
        self.ensure_auth()
        norm_status = (status or "all").lower()
        if norm_status in ("running", "ing", "active"):
            phases = PHASES_RUNNING
        elif norm_status in ("completed", "done", "complete"):
            phases = PHASES_COMPLETED
        else:
            phases = PHASES_ALL

        filters = {
            "phase": {"in": ",".join(phases)},
            "type": {"in": DEFAULT_TASK_TYPES},
        }
        res = self.request(
            "drive/v1/tasks",
            method="GET",
            params={
                "space": self.space,
                "limit": limit,
                "page_token": page_token,
                "filters": json.dumps(filters, separators=(",", ":")),
            },
        )
        return (res.get("tasks") if isinstance(res, dict) else None) or []

    def get_task(self, task_id: str) -> dict[str, Any]:
        """根据任务 ID 查询单个迅雷任务的完整详情 (`GET drive/v1/tasks`)."""
        self.ensure_auth()
        filters = {"id": {"in": str(task_id)}}
        res = self.request(
            "drive/v1/tasks",
            method="GET",
            params={
                "space": self.space,
                "filters": json.dumps(filters, separators=(",", ":")),
            },
        )
        tasks = (res.get("tasks") if isinstance(res, dict) else None) or []
        if not tasks:
            raise XunleiError("TASK_NOT_FOUND", f"未找到迅雷任务: {task_id}", endpoint="drive/v1/tasks")
        return tasks[0]

    def parse_url(
        self,
        urls: str | list[str],
        page_size: int = 2000,
    ) -> dict[str, Any]:
        """解析磁力/种子/HTTP/电驴链接 (`POST drive/v1/resource/list`)，获取资源列表与子文件索引."""
        self.ensure_auth()
        if isinstance(urls, (list, tuple)):
            urls_str = "\n".join(u.strip() for u in urls if u and u.strip())
        else:
            urls_str = str(urls).strip()

        # 若传入的是 40 位或 32 位纯 Hash，自动补全 magnet 前缀（与 xunlei_index.js Yrt 函数对齐）
        if re.fullmatch(r"[0-9a-zA-Z]{40}|[0-9a-zA-Z]{32}", urls_str):
            urls_str = f"magnet:?xt=urn:btih:{urls_str}"

        res = self.request(
            "drive/v1/resource/list",
            method="POST",
            data={"page_size": page_size, "urls": urls_str},
        )
        list_id = str(res.get("list_id") or "")
        list_obj = res.get("list") or {}
        resources = list_obj.get("resources") or []

        # 若首个资源是包含分页的 BT 目录，自动拉取剩余分页（对齐 fetchPartAllTask）
        if resources and isinstance(resources[0], dict):
            dir_obj = resources[0].get("dir")
            if isinstance(dir_obj, dict) and dir_obj.get("next_page_token") and list_id:
                next_token = str(dir_obj.get("next_page_token") or "")
                while next_token:
                    part_res = self.request(
                        f"drive/v1/resource/list/{list_id}",
                        method="GET",
                        params={"page_token": next_token},
                    )
                    part_list = (part_res.get("list") if isinstance(part_res, dict) else None) or {}
                    part_items = part_list.get("resources") or []
                    if part_items:
                        dir_obj.setdefault("resources", []).extend(part_items)
                        next_token = str(part_list.get("next_page_token") or "")
                    else:
                        break
                dir_obj["next_page_token"] = ""

        flat_files = flatten_resources(resources)
        return {
            "list_id": list_id,
            "urls": urls_str,
            "resources": resources,
            "files": flat_files,
            "raw": res,
        }

    def resolve_default_folder(self, folder_id: str | None = None) -> tuple[str, str]:
        """解析下载目标文件夹的 `(parent_folder_id, parent_folder_path)`."""
        self.ensure_auth()
        paths = self.get_download_paths()

        if folder_id:
            for p in paths:
                if str(p.get("Id") or "") == folder_id:
                    return folder_id, str(p.get("RealPath") or "")
            return folder_id, ""

        # 优先使用历史/默认下载目录列表中的第一项（通常为 /tmp/zfsv3/.../data/迅雷下载/）
        if paths and isinstance(paths[0], dict) and paths[0].get("Id"):
            return str(paths[0]["Id"]), str(paths[0].get("RealPath") or "")

        # 回退：从根目录文件列表中查找默认下载目录
        root_files = self.list_files(parent_id="")
        for f in root_files:
            f_params = f.get("params") or {}
            if f_params.get("default") == "true" or f_params.get("category_id") == "DownloadPath":
                return str(f.get("id") or ""), str(f_params.get("RealPath") or "")
        if root_files:
            first = root_files[0]
            return str(first.get("id") or ""), str((first.get("params") or {}).get("RealPath") or "")

        return "", ""

    def create_task(
        self,
        url: str,
        folder_id: str | None = None,
        sub_file_index: str | None = None,
        name: str | None = None,
        file_size: int | str | None = None,
        file_count: int | str | None = None,
        mime_type: str = "",
    ) -> dict[str, Any]:
        """创建迅雷下载任务 (`POST drive/v1/task`).

        完整复刻 `xunlei_index.js` 中 `addUrlTask` 的 JSON 结构体：
        1. 若未直接提供 `name` / `file_size`，先调用 `parse_url(url)` 解析资源名称、大小、子文件索引；
        2. 自动补全 `parent_folder_id` 与 `parent_folder_path`；
        3. 若是多文件 BT/磁力资源且未指定 `sub_file_index`，自动选取全部非目录子文件索引。
        """
        self.ensure_auth()
        clean_url = url.strip()
        if re.fullmatch(r"[0-9a-zA-Z]{40}|[0-9a-zA-Z]{32}", clean_url):
            clean_url = f"magnet:?xt=urn:btih:{clean_url}"

        resolved_name = name
        resolved_size = file_size
        resolved_count = file_count
        resolved_mime = mime_type
        resolved_sub_index = sub_file_index

        if resolved_name is None or resolved_size is None:
            parsed = self.parse_url(clean_url)
            resources = parsed.get("resources") or []
            flat_files = parsed.get("files") or []
            if not resources:
                raise XunleiError("PARSE_EMPTY", f"链接解析未返回任何可下载资源: {clean_url}", endpoint="drive/v1/resource/list")

            top_res = resources[0]
            top_meta = top_res.get("meta") or {}
            resolved_name = resolved_name or str(top_res.get("name") or "unnamed").strip()
            resolved_mime = resolved_mime or str(top_res.get("mime_type") or top_meta.get("mime_type") or "")

            dir_obj = top_res.get("dir")
            has_sub_dir = bool(top_res.get("is_dir")) or bool(
                isinstance(dir_obj, dict) and dir_obj.get("resources")
            )

            if resolved_sub_index is not None and resolved_sub_index != "":
                # 根据用户指定的 sub_file_index 过滤计算总大小与文件数
                wanted_indices = {
                    int(x.strip())
                    for x in str(resolved_sub_index).split(",")
                    if x.strip().isdigit()
                }
                matched_files = [f for f in flat_files if int(f.get("file_index", -1)) in wanted_indices]
                if matched_files:
                    resolved_size = sum(int(f.get("file_size") or 0) for f in matched_files)
                    resolved_count = len(matched_files)
                else:
                    resolved_size = int(top_res.get("file_size") or 0)
                    resolved_count = len(wanted_indices) or int(top_res.get("file_count") or 1)
            elif has_sub_dir and flat_files:
                # 多文件 BT/磁力默认全选所有子文件索引
                all_indices = [
                    str(f.get("file_index", 0))
                    for f in flat_files
                    if not f.get("is_dir") and f.get("file_index") is not None
                ]
                if all_indices:
                    resolved_sub_index = ",".join(all_indices)
                resolved_size = int(top_res.get("file_size") or sum(int(f.get("file_size") or 0) for f in flat_files))
                resolved_count = int(top_res.get("file_count") or len(flat_files) or 1)
            else:
                resolved_size = int(top_res.get("file_size") or 0)
                resolved_count = int(top_res.get("file_count") or 1)

        parent_id, parent_path = self.resolve_default_folder(folder_id)

        # 若指定了下载目录 ID，同步通知 device/createDownloadPath 记录常用目录（忽略非致命异常）
        if parent_id:
            try:
                self.request("device/download_path", method="POST", data={"file_id": parent_id})
            except Exception:
                pass

        clean_task_name = (resolved_name or "unnamed").strip().replace("\n", "")
        task_params: dict[str, Any] = {
            "target": self.space,
            "url": clean_url,
            "total_file_count": str(resolved_count if resolved_count is not None else 1),
            "parent_folder_id": parent_id,
            "mime_type": resolved_mime or "",
            "file_id": "",
        }
        if parent_path:
            task_params["parent_folder_path"] = parent_path
        if resolved_sub_index:
            task_params["sub_file_index"] = str(resolved_sub_index)

        payload: dict[str, Any] = {
            "type": TASK_TYPE_URL,
            "name": clean_task_name,
            "file_name": clean_task_name,
            "file_size": str(resolved_size if resolved_size is not None else 0),
            "space": self.space,
            "params": task_params,
        }
        return self.request("drive/v1/task", method="POST", data=payload)

    def _operate_task(self, task_id: str, action: str, task_type: str | None = None) -> dict[str, Any]:
        """内部通用任务状态变更方法 (`PATCH drive/v1/task`).

        对应前端 `operateTask`：
        `action` 取值为 `"pause"`（暂停）、`"running"`（恢复）、`"delete"`（连同本地文件删除）。
        """
        self.ensure_auth()
        resolved_type = task_type
        resolved_space = self.space
        if not resolved_type:
            try:
                task_obj = self.get_task(task_id)
                resolved_type = str(task_obj.get("type") or TASK_TYPE_URL)
                resolved_space = str(task_obj.get("space") or self.space)
            except Exception:
                resolved_type = TASK_TYPE_URL

        payload = {
            "space": resolved_space,
            "type": resolved_type,
            "id": str(task_id),
            "set_params": {
                "spec": json.dumps({"phase": action}, separators=(",", ":")),
            },
        }
        return self.request("drive/v1/task", method="PATCH", data=payload)

    def pause_task(self, task_id: str) -> dict[str, Any]:
        """暂停迅雷下载任务 (`PATCH drive/v1/task`, `spec: {"phase": "pause"}`)."""
        return self._operate_task(task_id, action="pause")

    def resume_task(self, task_id: str) -> dict[str, Any]:
        """恢复迅雷下载任务 (`PATCH drive/v1/task`, `spec: {"phase": "running"}`)."""
        return self._operate_task(task_id, action="running")

    def delete_task(
        self,
        task_ids: str | list[str],
        delete_file: bool = False,
    ) -> Any:
        """删除迅雷下载任务 (`DELETE drive/v1/tasks` 或 `PATCH drive/v1/task`).

        对齐前端 `handleDeleteConfirm`：
        - 当 `delete_file=False`（仅删除任务记录）时：调用 `DELETE drive/v1/tasks?space=<space>&task_ids=<id>`；
        - 当 `delete_file=True`（同时删除本地文件）时：调用 `PATCH drive/v1/task` (`spec: {"phase": "delete"}`)。
        """
        self.ensure_auth()
        if isinstance(task_ids, str):
            ids_list = [x.strip() for x in task_ids.split(",") if x.strip()]
        else:
            ids_list = [str(x).strip() for x in task_ids if str(x).strip()]

        if not ids_list:
            raise XunleiError("EMPTY_TASK_IDS", "请提供至少一个要删除的任务 ID")

        if delete_file:
            results = []
            for tid in ids_list:
                results.append(self._operate_task(tid, action="delete"))
            return {"deleted": ids_list, "delete_file": True, "results": results}

        res = self.request(
            "drive/v1/tasks",
            method="DELETE",
            params={
                "space": self.space,
                "task_ids": ids_list,
            },
            data={},
        )
        return {"deleted": ids_list, "delete_file": False, "raw": res}

    # ── 3. 目录浏览、下载路径与状态信息 ──

    def list_files(
        self,
        parent_id: str = "",
        limit: int = 100,
        only_folders: bool = True,
    ) -> list[dict[str, Any]]:
        """列出迅雷挂载空间下的文件或文件夹 (`GET drive/v1/files`)."""
        self.ensure_auth()
        params: dict[str, Any] = {
            "space": self.space,
            "parent_id": parent_id,
            "limit": limit,
            "page_token": "",
            "with": ["withCategoryDiskMountPath", "withCategoryDownloadPath"],
        }
        if only_folders:
            params["filters"] = json.dumps({"kind": {"eq": "drive#folder"}}, separators=(",", ":"))

        res = self.request("drive/v1/files", method="GET", params=params)
        return (res.get("files") if isinstance(res, dict) else None) or []

    def get_download_paths(self) -> list[dict[str, Any]]:
        """获取迅雷默认/历史下载目录列表 (`GET device/download_paths`)."""
        self.ensure_auth()
        res = self.request("device/download_paths", method="GET")
        if isinstance(res, list):
            return res
        return []

    def get_about(self) -> dict[str, Any]:
        """获取迅雷云盘/空间配额信息 (`GET drive/v1/about`)."""
        self.ensure_auth()
        res = self.request("drive/v1/about", method="GET", params={"space": self.space})
        return res if isinstance(res, dict) else {}

    def get_status(self) -> dict[str, Any]:
        """获取迅雷客户端综合状态、版本、账号会员信息、默认下载目录与磁盘挂载空间."""
        self.ensure_auth()
        watch_info = self.request(
            "device/info/watch",
            method="POST",
            params={"space": self.space},
            data={},
        )
        about_info = self.get_about()
        download_paths = self.get_download_paths()
        root_folders = self.list_files(parent_id="")

        return {
            "device_space": self.device_space,
            "space": self.space,
            "web_url": self.web_url,
            "runner": self.runner_task,
            "device_info": watch_info if isinstance(watch_info, dict) else {},
            "about": about_info,
            "download_paths": download_paths,
            "root_folders": root_folders,
        }
