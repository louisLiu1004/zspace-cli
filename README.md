# zspace-cli (`zs`)

极空间 (ZSpace NAS) 全功能命令行工具与全量 2,004 接口动态调用引擎。

---

## 简介

`zspace-cli`（可执行命令为 `zs`）是专为极空间 NAS 设计的现代化命令行工具与 MCP (Model Context Protocol) 服务端。通过逆向提取官方客户端 73 个业务模块的 2,004 个 API 元数据，提供涵盖存储监控、文件管理、迅雷下载、原生下载中心、Docker 容器与 Compose 编排、KVM 虚拟机、私人保险箱等全方位的操作能力，并支持在 Stdio 上运行标准 MCP 协议，无缝接入 Claude Desktop、Cursor、Antigravity 等各类 AI 助手。

---

## 前置条件

使用 `zs` 的最简前置条件：
- **本机已登录极空间桌面客户端**（macOS 或 Windows）。
- `zs` 会自动读取本机极空间客户端的本地会话缓存（如 macOS 的 `~/Library/Application Support/ZSpace/vuex.json`），免去手动输入账号密码或抓包获取 Token 的繁琐步骤。
- 若在无桌面客户端环境（如远程 Linux 服务器），可使用 `zs auth login` 通过局域网 IP 与账号密码独立登录获取凭证。

---

## 安装

推荐在虚拟环境中通过可编辑模式安装：

```bash
# 1. 克隆或进入项目根目录
cd zspace-cli

# 2. 安装项目依赖（使用 pip 或 uv）
pip install -e .

# 若需要运行开发与单元测试：
pip install -e ".[dev]"
```

安装完成后，即可在终端直接执行 `zs` 命令：

```bash
zs --version
zs check
```

---

## 命令总览

以下命令列表均经 `zs --help` 及各子命令 `--help` 实际核对，真实可用：

### 快捷与鉴权命令

| 命令 | 描述 |
| :--- | :--- |
| `zs check` | 检查极空间连接状态、存储池与磁盘空间概览（等同于 `zs auth check`） |
| `zs auth check` | 验证当前连接状态，展示登录用户、网关状态、存储池与磁盘空间概览 |
| `zs auth info` | 展示脱敏后的当前会话凭证与本地配置文件路径详情 |
| `zs auth login` | 通过局域网 IP + 账号密码（经 RSA 加密）独立获取并持久化 Token |

### API 动态引擎

| 命令 | 描述 |
| :--- | :--- |
| `zs api stats` | 统计展示内置的 73 个 Webpack 业务模块与 2,004 个 API 接口分布 |
| `zs api search <关键词>` | 在 2,004 个接口中按 URL、前端函数名或参数名模糊搜索并以 Rich 表格展示 |
| `zs api info <接口>` | 查看指定 API 的请求方法、URL、Content-Type、所属模块及推导参数 |
| `zs api call <接口>` | 直接动态调用任意极空间 API 接口并格式化输出返回结果 |

### 存储池与硬件健康 (`zs pool`)

| 命令 | 描述 |
| :--- | :--- |
| `zs pool ls` | 列出所有存储池容量与健康状态概览 (`/zspool/info`) |
| `zs pool info` | 查看存储池详情与空间分类分布 (`/system/diskusage3`) |
| `zs pool disks` | 查看设备硬盘插槽与所有物理硬盘健康状态 (`/zspool/hardware/info`) |
| `zs pool smart` | 查看物理硬盘完整 SMART 健康详情报告 (`/zspool/smart/report2`) |
| `zs pool cache` | 查看 SSD 读写缓存配置与命中率 (`/zspool/cache/hits`) |
| `zs pool usb` | 查看外接 USB / 虚拟 USB 与外置挂载设备 (`/zspool/vusb/status`) |
| `zs pool snapshot ls` | 列出存储池快照列表与定时计划状态 (`/zspool/snapshot/list`) |
| `zs pool snapshot create` | 创建存储池快照 (`/zspool/snapshot/create`) |
| `zs pool snapshot delete` | 删除存储池快照 (`/zspool/snapshot/delete`) |

### 文件管理 (`zs file`)

| 命令 | 描述 |
| :--- | :--- |
| `zs file ls <路径>` | 列出目录内容 (`/v2/file/list`)，支持文件大小、修改时间与类型展示 |
| `zs file tree <路径>` | 递归展示目录树结构 |
| `zs file info <路径>` | 查看文件/目录详细元数据与空间统计 |
| `zs file search <关键词>` | 全局索引搜索文件 (`/file_search/file_search`)，毫秒级检索全盘文件 |
| `zs file mkdir <路径>` | 新建文件夹 (`/v2/file/newdir`) |
| `zs file rename <路径> <新名称>` | 重命名文件或目录 (`/v2/file/modify`) |
| `zs file mv <源路径...> <目标目录>` | 移动文件或目录 (`/v2/file/move`) |
| `zs file cp <源路径...> <目标目录>` | 复制文件或目录 (`/v2/file/copy`) |
| `zs file rm <路径...>` | 删除文件或目录（默认移入个人回收站，支持 `--permanent` 彻底删除） |
| `zs file upload <本地文件> <远端目录>` | 上传本地文件到极空间（支持小文件直传与大文件分片断点续传） |
| `zs file download <远端路径> [本地保存路径]` | 从极空间流式下载文件，带实时进度条与错误识别 |
| `zs file unzip <压缩包路径>` | 在极空间服务端在线解压压缩包 (`/v2/file/decompress/make`) |
| `zs file zip <目标压缩包> <路径...>` | 在极空间服务端创建在线 ZIP 压缩任务 (`/v2/compression/create`) |
| `zs file large` | 服务端高速扫描大文件 (`/v2/file/find/large/*`) |
| `zs file dedup` | 重复文件查找与去重管理 (`/v2/fdd/*`) |
| `zs file trash ls` | 查看回收站文件列表 (`/v2/file/nb/list`) |
| `zs file trash restore <文件ID>` | 从回收站还原文件或目录 (`/v2/file/restore`) |
| `zs file trash clean` | 清空个人或公共回收站 |

### 内嵌迅雷下载 (`zs xl`)

| 命令 | 描述 |
| :--- | :--- |
| `zs xl ls` | 展示极空间迅雷下载任务列表（ID、名称、大小、进度、速度、状态） |
| `zs xl info <任务ID>` | 查看单个迅雷任务的详细参数与 VIP/SVIP 加速状态 |
| `zs xl parse <下载链接>` | 解析磁力/直链/电驴链接，查看内部文件明细与索引号 |
| `zs xl add <下载链接>` | 一键解析并添加磁力/HTTP/电驴链接到迅雷下载 |
| `zs xl pause <任务ID>` | 暂停指定的迅雷下载任务 |
| `zs xl resume <任务ID>` | 恢复指定的迅雷下载任务 |
| `zs xl rm <任务ID>` | 删除迅雷任务，支持 `--delete-file` 同时删除本地文件 |
| `zs xl dirs` | 查看迅雷默认下载目录与存储挂载目录 |
| `zs xl status` | 查看迅雷客户端状态、版本、账号会员等级与空间占用 |

### 原生下载中心 (`zs dl`)

| 命令 | 描述 |
| :--- | :--- |
| `zs dl ls` | 查看下载中心任务列表 (`/downloader/list`) 及队列详情 |
| `zs dl add <链接或种子>` | 添加下载链接、NAS 内种子或本地种子 |
| `zs dl start [任务ID]` | 开始/恢复单个下载任务或全部任务 (`--all`) |
| `zs dl stop [任务ID]` | 暂停单个下载任务或全部任务 (`--all`) |
| `zs dl rm [任务ID]` | 删除单个下载任务或批量删除全部任务 |
| `zs dl seed` | BT/PT 做种控制（全部开始做种 / 全部停止做种） |
| `zs dl config` | 查看或修改极空间原生下载中心并发与限速配置 |

### Docker 与 Compose (`zs docker` / `zs compose`)

| 命令 | 描述 |
| :--- | :--- |
| `zs docker ps` | 列出 Docker 容器及实时 CPU/内存占用、端口映射与编排归属 |
| `zs docker stats` | 查看容器实时 CPU、内存、网络速率与磁盘占用 |
| `zs docker start <容器>` | 启动指定 Docker 容器 (`/zdocker/container_op?action=start`) |
| `zs docker stop <容器>` | 停止指定 Docker 容器 (`/zdocker/container_op?action=stop`) |
| `zs docker restart <容器>` | 重启指定 Docker 容器 (`/zdocker/container_op?action=restart`) |
| `zs docker rm <容器>` | 删除指定 Docker 容器 (`/zdocker/container_op?action=remove`) |
| `zs docker logs <容器>` | 查看指定 Docker 容器日志 |
| `zs docker inspect <容器>` | 查看容器详细配置、挂载卷、环境变量与网络信息 |
| `zs docker top <容器>` | 查看容器内部正在运行的进程列表 |
| `zs docker to-compose <容器>` | 将现有容器配置逆向导出为 `docker-compose.yml` |
| `zs docker images` | 列出本地 Docker 镜像或精选常用镜像 |
| `zs docker search <关键词>` | 在 Docker Hub 或极空间镜像库中搜索镜像 |
| `zs docker tags <镜像名>` | 查询指定 Docker Hub 镜像的可用版本标签 |
| `zs docker pull <镜像名>` | 触发异步拉取 Docker 镜像任务 |
| `zs docker mirrors` | 查看 Docker 镜像源与加速器配置 |
| `zs docker info` | 查看 Docker 引擎版本、Compose 版本与运行状态 |
| `zs compose ls` | 列出所有 Docker Compose 编排项目及运行状态 |
| `zs compose info <项目>` | 查看 Compose 项目的容器列表与 `docker-compose.yml` 内容 |
| `zs compose logs <项目>` | 查看 Compose 项目编排日志 |
| `zs compose up <项目>` | 启动指定 Docker Compose 项目 |
| `zs compose down <项目>` | 停止（或删除）指定 Docker Compose 项目 |
| `zs compose restart <项目>` | 重启指定 Docker Compose 项目 |
| `zs compose rebuild <项目>` | 重新构建指定 Docker Compose 项目 |

### KVM 虚拟机 (`zs vm`)

| 命令 | 描述 |
| :--- | :--- |
| `zs vm ls` | 列出所有 KVM 虚拟机及运行状态 (`/v1/vm/domain/domainList`) |
| `zs vm info <虚拟机>` | 查看指定虚拟机的基础配置、虚拟磁盘与网卡详情 |
| `zs vm host` | 检查宿主机 KVM 虚拟化环境与 `zsvirt` 服务状态 |
| `zs vm start <虚拟机>` | 启动指定 KVM 虚拟机 |
| `zs vm stop <虚拟机>` | 关闭指定 KVM 虚拟机 |
| `zs vm reboot <虚拟机>` | 重启指定 KVM 虚拟机 |
| `zs vm pause <虚拟机>` | 暂停/挂起指定 KVM 虚拟机 |
| `zs vm resume <虚拟机>` | 恢复已暂停的 KVM 虚拟机 |
| `zs vm snapshot ls <虚拟机>` | 列出指定虚拟机的所有快照 |
| `zs vm snapshot create <虚拟机>` | 为指定虚拟机创建快照 |
| `zs vm snapshot restore <虚拟机>` | 将虚拟机恢复到指定快照 |
| `zs vm snapshot rm <虚拟机>` | 删除虚拟机的快照 |

### 私人保险箱与共享 (`zs safe` / `zs share`)

| 命令 | 描述 |
| :--- | :--- |
| `zs safe status` | 查看私人保险箱状态、密码提示与双备份信息 |
| `zs safe open` | 使用 RSA 加密密码解锁私人保险箱或加密文件夹 |
| `zs safe close` | 锁定关闭私人保险箱或加密文件夹 |
| `zs safe ls` | 浏览已解锁的私人保险箱目录内容 |
| `zs share ls` | 列出外链分享、闪电传与 NAS 内部共享及统计概览 |
| `zs share create <路径>` | 创建外链分享、闪电传或内部共享 |
| `zs share rm <分享ID>` | 删除或取消外链分享及内部共享 |

### 系统监控与只读服务 (`zs sys` / `zs net` / `zs smb` / `zs cloud` / `zs video` / `zs album`)

| 命令 | 描述 |
| :--- | :--- |
| `zs sys status` | 系统概览：机型、运行时间、总容量与已用容量、存储池状态 |
| `zs sys versions` | 系统与各内置组件版本（极相册、极影视、极下载等） |
| `zs sys ups` | UPS 电源状态与断电关机设置 |
| `zs sys upgrade` | 固件/服务升级信息与自动升级开关 |
| `zs sys sleep-check` | 诊断阻止硬盘休眠的因素并提供建议 |
| `zs net firewall` | 防火墙开关、网口默认策略与放行规则 |
| `zs net login-guard` | 登录失败锁定策略（IP 访问控制） |
| `zs net cert` | SSL 证书列表与有效期 |
| `zs smb status` | 汇总 Samba / FTP / WebDAV / DLNA 开启状态与端口 |
| `zs cloud status` | 各网盘（百度网盘/Google Drive 等）绑定状态与空间用量（已脱敏） |
| `zs video status` | 极影视服务运行状态 |
| `zs video libs` | 极影视影视库分类及影片数量 |
| `zs video latest` | 极影视首页「最近添加」合集 |
| `zs album home` | 极相册首页相册列表与「我喜欢」照片 |
| `zs album ai` | 极相册 AI 分析进度（人脸/场景/宠物/以图搜图） |
| `zs album db` | 极相册数据库健康与备份状态 |

### MCP 服务 (`zs mcp`)

| 命令 | 描述 |
| :--- | :--- |
| `zs mcp serve` | 启动 MCP stdio 服务（供 AI 助手子进程拉起） |
| `zs mcp tools` | 列出对外暴露的 MCP 工具清单 |
| `zs mcp config` | 输出可直接粘贴至 AI 客户端的配置 JSON 片段 |

---

## MCP 接入方法

`zs` 内置了遵循 Model Context Protocol (2024-11-05) 标准的 Server，能让 Claude Desktop、Cursor、Antigravity 等 AI 助手直接理解并调用极空间 NAS。

运行以下命令可自动输出配置片段：

```bash
zs mcp config
```

输出示例：

```json
{
  "mcpServers": {
    "zspace": {
      "command": "/path/to/venv/bin/zs",
      "args": [
        "mcp",
        "serve"
      ]
    }
  }
}
```

### 接入 Claude Desktop
打开 Claude Desktop 配置文件：
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

将上述 JSON 内容合并进 `mcpServers` 节点，重启 Claude Desktop 即可。

### 暴露的 MCP 工具
1. `zspace_status`：只读获取极空间存储池与磁盘概览。
2. `zspace_api_search`：在内置 2,004 个接口中按关键词检索接口路径与参数。
3. `zspace_api_call`：按目录元数据调用任意接口（写操作需显式确认）。

---

## 安全说明

1. **写操作默认安全确认**：
   - 在 MCP 模式下，包含写操作语义的接口（如重启、关机、删除文件/容器等）受到安全拦截规则保护。必须在参数中显式附带 `confirm_write=true` 才会向 NAS 发送真实网络请求，防止 AI 模型误触破坏性操作。
2. **凭证脱敏与存储安全**：
   - 终端打印凭证（如 `zs auth info`、`zs cloud status`）时，所有 Token、密码、手机号和邮箱均执行首尾脱敏遮盖处理。
   - **切勿将包含 Token 或密码的本地配置文件（如 `~/.zspace/config.json`、`vuex.json`）提交至 Git 公开仓库**。
3. **网络与接口安全**：
   - 所有密码传输均使用极空间服务端公钥进行 RSAES-PKCS1-v1_5 加密，杜绝明文传输。
