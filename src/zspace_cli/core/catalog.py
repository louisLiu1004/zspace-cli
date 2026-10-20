"""极空间 (ZSpace NAS) 全量 2,004 接口目录加载、检索与动态调用引擎.

基于逆向提取的 `src/zspace_cli/data/parsed_catalog.json`（涵盖 73 个 Webpack 业务模块、
2,004 个 API 条目），支持：
1. 模块分布统计 (`get_stats`)
2. 多维度模糊检索：按 URL、前端函数名 (`fn`)、模块名/ID、请求参数名搜索 (`search`)
3. 精确或模糊定位单个 API 元数据 (`resolve`)
4. 根据目录元数据（HTTP 方法、Content-Type、api_target、参数定义）一键发起调用 (`call_by_catalog`)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zspace_cli.core.client import ZSpaceClient

# 默认内置目录数据文件路径
CATALOG_FILE_PATH = Path(__file__).resolve().parent.parent / "data" / "parsed_catalog.json"

# 73 个 Webpack 模块 ID 到中文业务领域名称的映射表（逆向整理自 web_home 各 chunk）
MODULE_NAMES_CN: dict[str, str] = {
    "74922": "极相册 (ZAlbum 核心与 AI 识别)",
    "1297": "极影视 (ZVideo 海报墙/刮削/字幕)",
    "52324": "文件共享与网络协议 (Samba/WebDAV/NFS/FTP/同步)",
    "5219": "核心文件管理 (File/回收站/转码/解压/分享)",
    "74168": "系统设置与硬件监控 (风扇/UPS/灯光/通知/磁盘)",
    "33447": "Docker 引擎与 Portainer 容器管理",
    "94382": "新版 Docker 编排与镜像仓库 (ZDocker)",
    "41718": "账号认证与用户权限管理 (Auth/User/短信)",
    "83787": "ZSPool 存储池与 RAID 磁盘管理",
    "80149": "虚拟机管理 (VM / Libvirt)",
    "87857": "极音乐 v2 核心服务 (ZMusic)",
    "53733": "网络配置管理 (Wi-Fi/网卡/网关/债券/PPPoE)",
    "14530": "下载中心 (PT/BT/HTTP 引擎)",
    "20012": "闪电查杀与病毒防护 (ZVirusScan)",
    "14318": "邮件备份与管理中心 (MailManager)",
    "88861": "视频监控中心 (ZNVR 摄像头管理)",
    "2934": "百度网盘挂载与同步 (ZNetdisk)",
    "41692": "极课堂 / 教育资源管理 (ZEdu)",
    "62434": "iSCSI 存储区域网络管理 (ZSanManager)",
    "47786": "Google Drive 网盘同步",
    "95867": "OneDrive 网盘挂载与自动备份",
    "63932": "备份中心 v2 插件规则管理 (ZBakV2)",
    "81792": "应用商店与套件包管理 (AppStore)",
    "377": "记事本应用 (Notepad)",
    "8201": "极相册 AI 自然语言搜图 (RAG Search)",
    "23939": "极音乐歌单管理 (ZMusic Playlist)",
    "5600": "OnlyOffice 文档协作服务",
    "51899": "阿里云盘挂载与同步 (ADrive)",
    "33458": "有声读物应用 (ZAudio)",
    "55834": "115 网盘挂载与同步 (Yun115)",
    "14404": "极相册 AI 图片超分修复 (SuperResolution)",
    "50664": "天翼云盘挂载与同步 (TianyiClub)",
    "63775": "外链分享下载任务管理",
    "66709": "123 云盘挂载与同步 (Yun123)",
    "86673": "夸克网盘挂载与同步 (Kuake)",
    "46692": "桌面与移动端应用入口配置 (HomeApps)",
    "89194": "极影视电视直播 (ZVideo Live)",
    "1330": "微信机器人通知助手 (WXRobot)",
    "10230": "中国移动云盘同步 (Yun139)",
    "35485": "备份中心任务调度 (ZBakV2 Task)",
    "67288": "FTP / Rsync 远程备份下载",
    "85092": "全文搜索与搜索历史 (FileSearch)",
    "89794": "ZSPool 快照管理 (Snapshot)",
    "27706": "Web 首页与安全导航入口",
    "41081": "ZSPool eSATA 外置存储管理",
    "61540": "视频审阅与协作分享 (Review Share)",
    "83579": "StoragePool eSATA 外置盘管理",
    "1552": "Docker 容器化应用管理",
    "60746": "AI 实验室与本地大模型 (AiLab / Llama)",
    "69302": "文件标签管理 (Labels)",
    "8279": "Time Machine 苹果时光机备份",
    "38026": "文档同步服务 (DocSync)",
    "67420": "虚拟 USB 磁盘管理 (vUSB)",
    "99499": "SSL 证书管理与申请 (Cert)",
    "25926": "桌面主题与壁纸属性 (ThemeApp)",
    "97275": "私人保险箱 (Safebox)",
    "15786": "重复文件查找与清理 (FDD)",
    "49667": "Dropbox 网盘同步",
    "88175": "系统防火墙管理 (ZFirewall)",
    "8373": "动态域名解析服务 (ZDDNS)",
    "37138": "跨设备极空间互备 (CrossDevice Backup)",
    "41241": "备份中心资源池管理 (ZBakCenter)",
    "53057": "文件收集箱 (Receiver)",
    "35869": "极连接外链密钥协商 (UD Agree)",
    "37421": "通用键值存储服务 A (KV Store)",
    "4863": "通用键值存储服务 B (KV Store)",
    "64681": "NVIDIA 显卡驱动管理 (GPU Driver)",
    "92840": "NAS 间文件夹共享 (NASShare)",
    "7229": "外链分享视频转码播放地址",
    "11458": "文件救援恢复工具 (FileRescue)",
    "12684": "微信文件自动备份绑定 (WeixinBackup)",
    "49685": "云端 AI 辅助接口",
    "64095": "极相册初始化引导",
}


def clean_catalog_url(raw_url: str) -> str:
    """清理静态分析提取出的动态表达式或引号包裹的 URL."""
    u = raw_url.strip()
    # 处理如 '"/zspool/external/rename")' 的尾部残留
    if u.startswith('"') and u.endswith('")'):
        u = u[1:-2]
    u = u.strip("\"'")
    # 处理三元表达式如 't.isDocker?"/zdocker/AppStore/v1/pkg/restart":"/AppStore/v1/pkg/restart"'
    if "?" in u and ":" in u and ('"' in u or "'" in u):
        parts = [p.strip("\"' ") for p in u.split(":") if "/" in p]
        if parts:
            u = parts[-1].split('"')[0].split("'")[0]
    # 处理模板前缀 '{{d}}/v2/ud/key/pub'
    if u.startswith("{{") and "}}" in u:
        u = u.split("}}", 1)[1]
    return u


@dataclass(frozen=True)
class ApiEntry:
    """单个极空间 API 接口元数据定义."""

    module_id: str
    module_name: str
    js_file: str
    fn: str
    exp: str
    method: str
    url: str
    raw_url: str
    content_type: str
    api_target: str
    params: list[str] = field(default_factory=list)

    @property
    def is_json(self) -> bool:
        """判断该接口是否要求 application/json 请求体."""
        return "application/json" in (self.content_type or "").lower()

    @property
    def is_multipart(self) -> bool:
        """判断该接口是否为 multipart/form-data 请求."""
        return "multipart/form-data" in (self.content_type or "").lower()

    @property
    def display_content_type(self) -> str:
        """用于 UI 展示的请求格式名称."""
        if self.content_type:
            return self.content_type
        if self.method.upper() == "GET":
            return "query"
        return "application/x-www-form-urlencoded"

    @property
    def qualified_fn(self) -> str:
        """模块ID.函数名 形式的唯一限定名."""
        return f"{self.module_id}.{self.fn}"


@dataclass
class ModuleStats:
    """单个 Webpack 模块的 API 统计信息."""

    module_id: str
    module_name: str
    js_file: str
    entry_count: int
    methods: dict[str, int]
    sample_urls: list[str]


class ApiCatalog:
    """极空间全量 API 目录管理器."""

    def __init__(self, catalog_path: Path | str | None = None):
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_FILE_PATH
        self._raw_modules: dict[str, dict[str, Any]] = {}
        self.entries: list[ApiEntry] = []
        self._by_url: dict[str, list[ApiEntry]] = {}
        self._by_fn: dict[str, list[ApiEntry]] = {}
        self._load()

    def _load(self) -> None:
        """从 parsed_catalog.json 加载全部 73 个模块与 2,004 个接口."""
        if not self.catalog_path.exists():
            raise FileNotFoundError(f"未找到 API 目录数据文件: {self.catalog_path}")

        raw_data: dict[str, dict[str, Any]] = json.loads(
            self.catalog_path.read_text(encoding="utf-8")
        )
        self._raw_modules = raw_data

        for mod_id, mod_info in raw_data.items():
            js_file = str(mod_info.get("file") or "")
            mod_name = MODULE_NAMES_CN.get(str(mod_id), f"模块 {mod_id}")
            for item in mod_info.get("entries") or []:
                raw_url = str(item.get("url") or "")
                cleaned_url = clean_catalog_url(raw_url)
                fn_name = str(item.get("fn") or "")
                method = str(item.get("method") or "POST").upper()
                params = [str(p) for p in (item.get("params") or [])]

                entry = ApiEntry(
                    module_id=str(mod_id),
                    module_name=mod_name,
                    js_file=js_file,
                    fn=fn_name,
                    exp=str(item.get("exp") or ""),
                    method=method,
                    url=cleaned_url,
                    raw_url=raw_url,
                    content_type=str(item.get("content_type") or ""),
                    api_target=str(item.get("api_target") or ""),
                    params=params,
                )
                self.entries.append(entry)
                self._by_url.setdefault(cleaned_url.lower(), []).append(entry)
                self._by_fn.setdefault(fn_name.lower(), []).append(entry)

    @property
    def module_count(self) -> int:
        """总模块数（73）."""
        return len(self._raw_modules)

    @property
    def total_entries(self) -> int:
        """总接口条目数（2,004）."""
        return len(self.entries)

    @property
    def unique_url_count(self) -> int:
        """去重后的唯一 URL 路由数."""
        return len(self._by_url)

    def get_module_stats(self) -> list[ModuleStats]:
        """获取按接口数量降序排列的 73 个模块统计列表."""
        stats: list[ModuleStats] = []
        for mod_id, mod_info in self._raw_modules.items():
            raw_entries = mod_info.get("entries") or []
            methods_cnt: dict[str, int] = {}
            sample_urls: list[str] = []
            for item in raw_entries:
                m = str(item.get("method") or "POST").upper()
                methods_cnt[m] = methods_cnt.get(m, 0) + 1
                u = clean_catalog_url(str(item.get("url") or ""))
                if u and len(sample_urls) < 3 and u not in sample_urls:
                    sample_urls.append(u)
            stats.append(
                ModuleStats(
                    module_id=str(mod_id),
                    module_name=MODULE_NAMES_CN.get(str(mod_id), f"模块 {mod_id}"),
                    js_file=str(mod_info.get("file") or ""),
                    entry_count=len(raw_entries),
                    methods=methods_cnt,
                    sample_urls=sample_urls,
                )
            )
        stats.sort(key=lambda s: (-s.entry_count, int(s.module_id) if s.module_id.isdigit() else 0))
        return stats

    def get_summary_counts(self) -> dict[str, Any]:
        """返回全局统计字典（按 HTTP 方法、Content-Type、api_target 汇总）."""
        by_method: dict[str, int] = {}
        by_ct: dict[str, int] = {}
        by_target: dict[str, int] = {}
        for e in self.entries:
            by_method[e.method] = by_method.get(e.method, 0) + 1
            ct = e.display_content_type
            by_ct[ct] = by_ct.get(ct, 0) + 1
            tgt = e.api_target or "direct (NAS)"
            by_target[tgt] = by_target.get(tgt, 0) + 1
        return {
            "modules": self.module_count,
            "total_entries": self.total_entries,
            "unique_urls": self.unique_url_count,
            "by_method": by_method,
            "by_content_type": by_ct,
            "by_target": by_target,
        }

    def search(
        self,
        keyword: str,
        module_id: str | None = None,
        method: str | None = None,
        limit: int = 100,
    ) -> list[ApiEntry]:
        """在 2,004 个接口中按 URL、前端函数名、模块名称或参数名模糊搜索."""
        kw = keyword.strip().lower()
        mod_filter = module_id.strip() if module_id else None
        method_filter = method.strip().upper() if method else None

        exact_matches: list[ApiEntry] = []
        prefix_matches: list[ApiEntry] = []
        fuzzy_matches: list[ApiEntry] = []

        for entry in self.entries:
            if mod_filter and entry.module_id != mod_filter:
                continue
            if method_filter and entry.method != method_filter:
                continue

            if not kw:
                fuzzy_matches.append(entry)
                continue

            url_low = entry.url.lower()
            fn_low = entry.fn.lower()
            qfn_low = entry.qualified_fn.lower()
            mod_low = entry.module_name.lower()
            params_low = [p.lower() for p in entry.params]

            if kw in (url_low, fn_low, qfn_low):
                exact_matches.append(entry)
            elif url_low.startswith(kw) or fn_low.startswith(kw):
                prefix_matches.append(entry)
            elif (
                kw in url_low
                or kw in fn_low
                or kw in mod_low
                or entry.module_id == kw
                or any(kw in p for p in params_low)
            ):
                fuzzy_matches.append(entry)

        combined = exact_matches + prefix_matches + fuzzy_matches
        return combined[:limit] if limit > 0 else combined

    def resolve(self, url_or_fn: str) -> list[ApiEntry]:
        """根据 URL（如 `/zspool/info`）、`module_id.fn`（如 `83787.getZspoolInfo`）或函数名查找接口定义."""
        target = url_or_fn.strip()
        low = target.lower()

        # 1. 尝试 module_id.fn 精确匹配
        if "." in target and not target.startswith("/"):
            mod_part, fn_part = target.split(".", 1)
            matched = [
                e
                for e in self.entries
                if e.module_id == mod_part and e.fn.lower() == fn_part.lower()
            ]
            if matched:
                return matched

        # 2. 尝试 URL 精确匹配（自动补齐或忽略前导 '/'）
        if low in self._by_url:
            return list(self._by_url[low])
        if not low.startswith("/") and f"/{low}" in self._by_url:
            return list(self._by_url[f"/{low}"])

        # 3. 尝试前端函数名 fn 精确匹配
        if low in self._by_fn:
            return list(self._by_fn[low])

        # 4. 退化为子串模糊搜索
        return self.search(target, limit=20)

    def call_by_catalog(
        self,
        client: ZSpaceClient,
        url_or_fn: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        method_override: str | None = None,
        check_code: bool = False,
    ) -> tuple[ApiEntry | None, Any]:
        """根据接口目录元数据自动选择请求方法、Content-Type 与网关发起调用.

        若传入的 URL 不在 2,004 个已收录目录中（如主进程特有端点），
        依然支持直接作为原始 URL 路径发起请求，此时返回 `(None, response_data)`。
        """
        matches = self.resolve(url_or_fn)
        entry: ApiEntry | None = None

        # 优先选取精确匹配项
        norm_q = url_or_fn.strip().lower()
        for m in matches:
            if (
                m.url.lower() == norm_q
                or m.url.lower() == f"/{norm_q}"
                or m.fn.lower() == norm_q
                or m.qualified_fn.lower() == norm_q
            ):
                entry = m
                break
        if entry is None and len(matches) == 1:
            entry = matches[0]

        if entry is not None:
            http_method = (method_override or entry.method or "POST").upper()
            endpoint = entry.url
            use_json = entry.is_json or (json_body is not None)
            api_target = entry.api_target
        else:
            # 未命中目录或存在歧义但用户输入了一个路径（以 '/' 开头）
            if url_or_fn.strip().startswith(("/", "http://", "https://")):
                http_method = (method_override or "POST").upper()
                endpoint = url_or_fn.strip()
                use_json = json_body is not None
                api_target = ""
            elif matches:
                # 取第一个最相关匹配
                entry = matches[0]
                http_method = (method_override or entry.method or "POST").upper()
                endpoint = entry.url
                use_json = entry.is_json or (json_body is not None)
                api_target = entry.api_target
            else:
                raise KeyError(f"未在 API 目录中找到匹配的接口: {url_or_fn}")

        resp_data = client.request(
            method=http_method,
            endpoint=endpoint,
            params=params if http_method == "GET" else None,
            data=params if http_method != "GET" and not use_json else None,
            json_data=json_body if json_body is not None else (params if use_json else None),
            request_as_json=use_json,
            api_target=api_target,
            check_code=check_code,
        )
        return entry, resp_data


# 全局单例缓存，避免重复解析 475KB JSON
_GLOBAL_CATALOG: ApiCatalog | None = None


def get_catalog() -> ApiCatalog:
    """获取全局共享的 ApiCatalog 实例."""
    global _GLOBAL_CATALOG
    if _GLOBAL_CATALOG is None:
        _GLOBAL_CATALOG = ApiCatalog()
    return _GLOBAL_CATALOG
