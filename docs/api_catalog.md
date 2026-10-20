# 极空间 (ZSpace NAS) 客户端全量 API 字典与请求构造规范

> **逆向工程与代码提取来源说明**
> 1. **Electron 桌面客户端壳层** (`/Applications/极空间.app/Contents/Resources/app.asar` -> `.asar_unpacked/`)：
>    - 主进程：`.asar_unpacked/background.js`（内置本地代理调度、分片上传中心、断点续传、音视频播放控制、Syncthing 同步、本地挂载等，调用底层 `sdk/libzrpc.dylib`）
>    - 预加载桥接：`.asar_unpacked/preload.js`
>    - 渲染层登录与设备外壳：`.asar_unpacked/static/js/app.d3d20628.js`、`chunk-6bcad85c.70caf412.js`、`chunk-6e4bd61c.de56d9fe.js`
> 2. **NAS 核心 Web 前端层** (`http://127.0.0.1:13579/home/` -> `.asar_unpacked/web_home/`)：
>    - 极空间桌面端完成登录后，主界面实际加载由本地代理转发的 NAS Web 应用 (`/home/index.html`)。
>    - 核心主包：`index.4191bbea.js`、`34693.faa26133.js` 以及 **886 个按需加载的异步业务 Chunk (`async/*.js`)**。
>    - 全量扫描共提取出 **73 个独立 Webpack API 模块**、共计 **1,760+ 个不重复的 HTTP API 路由**，并通过 AST/正则静态分析关联了前端语义方法名与调用点参数。

---

## 一、 架构通信机制与本地代理隧道

极空间 macOS 客户端在启动并登录设备后，会通过 IPC (`MainThreadAction` -> `simpleStartSerivce`) 调用 `libzrpc.dylib` 在本地监听代理端口，将 HTTP 请求通过加密隧道或局域网转发至 NAS：

| 服务类型 | 本地监听地址 | 远端目标端口 / 说明 |
|---|---|---|
| **NAS 核心管理 / Web API 代理** | `http://127.0.0.1:13579` | 映射 NAS 主服务端口（默认 `5055` HTTP / `5050` HTTPS，文件上传下载可通过 `remote_port=8050` 路由） |
| **Syncthing 文件同步服务** | `http://127.0.0.1:13581` | 桌面端双向/单向文件夹同步后台服务 |
| **Docker 容器应用动态代理** | `http://127.0.0.1:<动态端口>` | 每次在客户端打开容器 Web UI 时由主进程动态分配（常见范围 `9990–10150`） |

### 本地凭证持久化 (`vuex.json`)
桌面客户端将当前登录态保存在 `~/Library/Application Support/zspace/vuex.json`：
- **明文模式**：默认以 `{` (ASCII `123`) 开头的 UTF-8 JSON 存储。
  - `state.user.token`：核心会话鉴权 Token
  - `state.nas.nasId`：设备唯一标识 `nasid`
  - `state.nas.webBase`：当前本地代理根地址（通常为 `http://127.0.0.1:13579`）
  - `state.app.deviceId`：客户端设备 ID (`device_id`)
  - `state.app.device`：客户端设备名称（如 `Mac（Mac-mini）`）
  - `state.app.version`：客户端版本号
- **加密模式回退**（见 `app.d3d20628.js`）：若文件首字节非 `{`，客户端使用硬编码密钥 `"08dbab37279e24852d8f2a11795c143f"`，经 `crypto.scryptSync(key, "salt", 32)` 派生 32 字节密钥，取文件前 16 字节为 IV 进行 `aes-256-cbc` 解密。

---

## 二、 网络请求封装规范 (Axios Interceptors)

客户端中存在两套高度相似的 Axios 拦截器封装：**Web 核心业务层 (`index.4191bbea.js` 模块 `89904`)** 与 **Electron 桌面外壳层 (`app.d3d20628.js` 模块 `b775` / `background.js`)**。

### 1. 目标网关路由 (`apiTarget`)
在 Axios 请求配置 `config.apiTarget` 中决定 URL 前缀：
- 未指定或 `"direct"`：使用当前代理地址 `http://127.0.0.1:13579`（若处于云端集群模式则拼接 `https://${clusterHost}`）。
- `"server"` 或 `"email"`：路由至极空间云端中心网关 `https://api.zenithspace.net`（用于图形验证码、短信/邮件验证码、闪电传/云端通知等）。
- `"share"`：路由至外链分享网关 `https://znas.cn`。

### 2. 公共参数注入 (`appendParams`)
除 `/docker/api/*`、跨设备远程请求 (`forRemoteNas: true`)、以及显式声明 `requestAsJson: true` 的请求外，拦截器自动向请求参数（`GET` 注入 `params`，`POST` 注入 `data`）追加以下公共字段：

| 字段名 | Web 端 (`index.4191bbea.js`) | Electron 壳层 (`background.js`) | 说明 |
|---|---|---|---|
| `token` | `state.user.token` | `state.user.token` | 用户登录鉴权 Token |
| `nasid` | `state.nas.nasId` | `state.nas.nasId` (Cookie 中为 `nas_id`) | 目标 NAS 设备 ID |
| `plat` | `"web"` | `"pc"` | 平台标识 |
| `version` | `state.app.version` (如 `"2.3.2026042401"`) | `state.app.version` (如 `"1.0"`) | 协议/前端版本 |
| `device_id` | `state.app.deviceId` | `state.app.deviceId` | 客户端唯一设备标识 |
| `device` | `state.app.device` | `state.app.device` | 客户端设备名 |
| `_l` | `state.language.language` (`"zh_cn"`) | `state.nas.locale` (`"zh_cn"`) | 语言环境 |

> **模块特例**：极相册模块（Module `74922`）通过内部包装函数 `n(t)` 额外注入公共参数 `app: "album"`；主进程视频/文件接口额外在 Cookie 中注入 `app: "video"` 或 `app: "file"`。

### 3. URL 防缓存/防重放尾缀 (`rnd` / `drnd` & `webagent=v2`)
每次发起请求时，拦截器生成时间戳随机数：`o = Date.now() + "_" + Math.floor(Math.random() * 10000)`：
- **Web 业务层 (`index.4191bbea.js`)**：
  - `POST` 请求：在 URL 末尾强制拼接 `?&rnd=${o}&webagent=v2`（若已有 `?` 则拼接 `&rnd=${o}&webagent=v2`）。
  - `GET` 请求：在 Query 参数中注入 `rnd=${o}&webagent=v2`。
- **Electron 外壳层 (`app.d3d20628.js` / `background.js`)**：
  - `POST` 请求：在 URL 末尾拼接 `?&drnd=${o}`。
  - `GET` 请求：在 Query 参数中注入 `drnd=${o}`。

### 4. 请求体序列化与 `Content-Type` 规范
1. **默认表单模式 (`application/x-www-form-urlencoded`)**：
   - 绝大多数 `POST` 接口默认采用此模式。
   - **数组参数编码规则**：自定义 `transformRequest` 将数组属性 `paths: ["/a", "/b"]` 序列化为带方括号的形式：`paths[]=%2Fa&paths[]=%2Fb`，标量属性序列化为 `key=encodeURIComponent(val)`。
2. **JSON 模式 (`application/json`)**：
   - 当请求配置包含 `requestAsJson: true` 或 `headers: {"Content-Type": "application/json"}` 时（广泛用于 `/zdocker/*`、`/network-settings/v1/*`、`/zvideo/live/*`、`/AppStore/v1/*` 等新版接口），请求体直接 `JSON.stringify(data)`，且**不在 JSON body 内混入表单公共参数**（鉴权依赖 Header `Cookie`）。
3. **二进制流与表单上传 (`application/octet-stream` / `multipart/form-data`)**：
   - 小文件直传 `/v2/file/create` 与大文件分片上传 `/v2/file/upload` 使用 `application/octet-stream`，元数据通过 HTTP Header 传递。
   - BT 种子上传 `/downloader/add/torrent`、`/downloader/preprocess/torrent/parse/upload` 使用 `multipart/form-data`。

### 5. 特殊请求头 (Headers) 与跨系统鉴权
- **Cookie 鉴权头**：
  在客户端与 CLI 直连 `127.0.0.1:13579` 时，将公共参数格式化为 `Cookie` 头：
  `app=video; token=<url_encoded_token>; plat=pc; version=1.0; device_id=<id>; device=<url_encoded_device>; _l=zh_cn; nas_id=<nasId>`（注意 `nasid` 在 Cookie 中键名为 `nas_id`）。
- **Docker Portainer 专有鉴权 (`Authorization`)**：
  当请求 URL 包含 `/docker/api`（且非 `/docker/api/auth`）或 `/settings?create_container` 时，拦截器自动注入：
  `Authorization: <dockerToken>`（该 JWT Token 通过调用 `POST /docker/api/auth` 获取）。
- **跨设备远程 NAS 鉴权 (`forRemoteNas`)**：
  当访问其他极空间设备（如闪电传/远程挂载，Module `52324` 函数 `d(t, e)`）时，设置 `forRemoteNas: true`，并在 Header 中注入：
  `rtoken: <remote_token>`, `rdeviceid: <device_id>`, `rdevice: <device>`, `ruser: <username>`。
- **Electron 主进程伪装 Header**：
  在非浏览器环境的 Node 主进程请求中（`background.js`），若未开启开发模式，会自动注入：
  `Connection: keep-alive`, `Host: www.baidu.com`, `Referer: http://www.baidu.com`, `User-Agent: Mozilla/5.0 ... zspace/3.1.7185 ...`。

---

## 三、 签名、哈希与加解密算法细节

通过对客户端源码的全量排查，**极空间常规业务 API 并不对每个请求的参数做 HMAC/MD5 `sign` 签名校验**，而是依赖 `token` + `nasid` + `device_id` 鉴权。但以下 4 个特定场景使用了加密或签名算法：

### 1. 账号登录与保险箱密码 RSA 加密 (`GET /key/pub` + `POST /auth/login`)
位于 `chunk-6e4bd61c.de56d9fe.js` 与 `index.4191bbea.js`：
1. 登录前先调用 `GET /key/pub`，返回 `{ key: "<RSA_Public_Key>", sign: "...", name: "<NAS名称>" }`。
2. 使用 `JSEncrypt`（RSA PKCS#1 v1.5）加载 `data.key` 作为公钥。
3. **对账号 (`username`) 和去首尾空格后的密码 (`password`) 分别执行 RSA 加密**：
   - 若使用服务端返回的 `nasPubKey`：直接 `rsa.encrypt(str)` 输出 Base64 密文。
   - 若回退使用客户端内置默认公钥 `u`：按每 50 字符切片加密，各段 Base64 密文以 `~` 拼接。
4. 将加密后的 `{ username: encodeName, password: encodePassword, sms_proof?: "..." }` 提交至 `POST /auth/login`。

### 2. 大文件分片上传会话 `uuid` 签名 (`POST /v2/file/upload`)
位于 `background.js`：
- 客户端分片上传大文件时无需先调用服务端注册会话，而是本地按以下公式生成唯一会话 `uuid`：
  ```javascript
  // 注意：Math.ceil(lastModified) 与 size 先做数值相加，转为字符串后再与目标完整路径拼接
  const uuid = md5(String(Math.ceil(file.lastModified) + file.size) + targetFullPath);
  ```
- 断点续传查询 `GET /v2/file/tmpinfo?path=<targetFullPath>&uuid=<uuid>` 使用完全相同的 `uuid` 计算公式。

### 3. 极连接 / 外链分享密钥协商 (`/v2/ud/key/pub` & `/v2/ud/agree`)
位于 `index.4191bbea.js`（紧邻模块 `89904`）：
1. 生成随机串 `g = uuid + "_" + Math.random() + Date.now()`，计算 `h = sha256(g).digest("hex")`。
2. 用 RSA 公钥 `qcPubKey` 加密 `h` 得到 `rid`，调用 `POST /v2/ud/agree` 传入 `{ uuid, rid, key_id }`。
3. 收到响应 `{ agree_info, rid }` 后，将 `h` 的 ASCII 字节与 `Buffer.from(res.data.rid, "base64")` 奇偶交替穿插写入 2 倍长度的 Buffer `D`（即 `D[2*i] = h.charCodeAt(i)`, `D[2*i+1] = x[i]`）。
4. 计算 `P = sha256(D).digest()`，取 `P.slice(0, 32)` 作为 `aes-256-cbc` 的 Key，取 `P.slice(0, 16)` 作为 IV，解密 `agree_info` 并缓存会话密钥用于后续加密传输。

### 4. IPv6 代理查询签名 (`Query-Token`)
位于 `index.4191bbea.js`：
- 请求 `GET https://proxy.zenithspace.net/nasipv6info?plat=web&rnd=${ts}` 时，需在 Header 携带 `{ id: nasId, "Query-Token": sign }`：
  ```javascript
  const ts = Math.floor(Date.now() / 1000);
  const raw = `nas_id=${nasId}&plat=web&rnd=${ts}`;
  const queryToken = sha256(raw).digest("hex");
  ```

---

## 四、 核心业务模块 API 全量路由字典

### 4.1 文件管理、搜索、回收站、保险箱与分享模块 (共 199 个端点)
涵盖 Webpack 模块 `5219` (核心文件管理/回收站/转码/压缩)、`85092` (全文与 Office 搜索)、`97275` (保险箱 Safebox)、`69302` (文件标签)、`15786` (重复文件查找 FDD)、`377` (记事本 Notepad)、`61540` (视频审阅分享)、`53057` (文件收集箱 Receiver)。

> **补充 Electron 主进程 (`background.js`) 专有文件传输接口**：
> - `POST /v2/file/create`：小文件直传（Body 为二进制流，Header `path` 为 UTF-8 目标路径）
> - `POST /v2/file/upload?remote_port=8050&drnd={ts}&uuid={uuid}`：大文件分片上传（Body 为分片二进制流，Header 含 `path`, `size`, `uuid`, `seek`, `split=1`, `modify_time`, `rename=0`, `request-purpose=4`）
> - `GET /v2/file/tmpinfo?path={path}&uuid={uuid}`：查询未完成的分片上传偏移量 `data.size`
> - `GET /v2/file/download?path={path}&remote_port=8050`：文件下载（支持 `Range` 头）
> - `GET /v2/file/decrypt/download`：加密目录/相册文件解密下载（参数 `encrypted_path`, `from`）

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/file_search/skip_dir/list` | `fileSearchSkipList` | `form-urlencoded` | - |
| `POST` | `/file_search/skip_dir/add` | `fileSearchSkipAdd` | `form-urlencoded` | `paths` |
| `POST` | `/file_search/skip_dir/delete` | `fileSearchSkipDelete` | `form-urlencoded` | `paths` |
| `GET` | `/transcode/setting/load` | `transcodeSetting` | `query` | - |
| `POST` | `/transcode/setting/set` | `setTanscode` | `form-urlencoded` | `setting_key`, `setting_value` |
| `POST` | `/transcode/traversal` | `transcodeTask` | `form-urlencoded` | `action`, `file_path` |
| `POST` | `/transcode/video/process/tasks` | `videoProcessTasks` | `form-urlencoded` | `num`, `start`, `state` |
| `POST` | `/transcode/video/process/delete` | `videoProcessDelete` | `form-urlencoded` | `id` |
| `POST` | `/transcode/video/process/clear` | `videoProcessClear` | `form-urlencoded` | `scope` |
| `POST` | `/transcode/clean/thumb` | `cleanThumb` | `form-urlencoded` | - |
| `POST` | `/v2/file/pin` | `setPin` | `form-urlencoded` | `cancel`, `path` |
| `POST` | `/v2/file/decompress/setpwd` | `uncompressTaskPwd` | `form-urlencoded` | `forall`, `id`, `index`, `pwd`, `skip` |
| `POST` | `/v2/file/decompress/make` | `addUncompressTask` | `form-urlencoded` | - |
| `POST` | `/v2/file/decompress/dir` | `addDirUncompressTask` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/v1/openfile` | `createOnlyofficeFile` | `form-urlencoded` | - |
| `GET` | `https://zrpl-ucloud.zenithspace.net` | `getShareHost` | `query (direct)` | - |
| `POST` | `/system/pool/name` | `renameStorage` | `form-urlencoded` | `key`, `name` |
| `POST` | `/api/fileshare_service/poolname/sync` | `syncPoolName` | `form-urlencoded` | `stype` |
| `POST` | `/file_search/search_log/delete` | `delSearchHistory` | `form-urlencoded` | `names` |
| `POST` | `/file_search/search_log/get` | `getSearchHistory` | `form-urlencoded` | `search_type` |
| `POST` | `/v2/file/setdwperiod` | `setDwlistTimer` | `form-urlencoded` | `period` |
| `GET` | `/transcode/mediainfo/video` | `getVideoInfo` | `query` | `file_path` |
| `GET` | `/transcode/decrypt/mediainfo/video` | `getShareVideoInfo` | `query` | - |
| `GET` | `/transcode/mediainfo/image` | `getImgInfo` | `query` | `file_path` |
| `GET` | `/transcode/decrypt/mediainfo/image` | `getEncryptImgInfo` | `query` | - |
| `GET` | `/transcode/mediainfo/exif` | `getImgExif` | `query` | `file_path` |
| `GET` | `/transcode/decrypt/mediainfo/exif` | `getEncryptImgExif` | `query` | `encrypted_path`, `from` |
| `POST` | `/v2/file/restoreall` | `restoreAll` | `form-urlencoded` | `paths` |
| `POST` | `/v2/file/nb/list` | `getRecycleFileList` | `form-urlencoded` | `num`, `path`, `start` |
| `POST` | `/v2/share/statics` | `staticsShare` | `form-urlencoded` | - |
| `POST` | `/v2/share/modify/by` | `modifyShare` | `form-urlencoded` | `newState`, `state`, `stype` |
| `POST` | `/v2/share/delete/by` | `deleteShare` | `form-urlencoded` | `state`, `stype` |
| `POST` | `/v2/file/merge_restore` | `restoreFile` | `form-urlencoded` | `dup`, `file_id`, `path` |
| `POST` | `/v2/file/empty_dir` | `cleanDir` | `form-urlencoded` | `paths` |
| `GET` | `/settings?root_list=true` | `getRecycleExtDev` | `query` | - |
| `POST` | `"/zspool/external/rename")` | `mountDev` | `form-urlencoded` | - |
| `POST` | `/redirect/v2/share/statisticbytoken` | `addSizeTask` | `form-urlencoded` | - |
| `POST` | `/redirect/v2/share/statistictask` | `checkSizeTask` | `form-urlencoded` | `cancel`, `id` |
| `POST` | `/downloader/share/add` | `shareDownloadAdd` | `form-urlencoded` | - |
| `POST` | `/nasid/pubkey` | `getNasPubKey` | `form-urlencoded (server)` | - |
| `POST` | `/v2/short/create` | `getShortLink` | `form-urlencoded (server)` | - |
| `POST` | `/v2/file/info` | `fileInfo` | `form-urlencoded` | `file_path`, `path` |
| `POST` | `/file_search/file_search` | `fileSearch` | `form-urlencoded` | `file_path`, `ftype`, `is_dir`, `max_size`, `min_size`, `name`, `num`, `order_by`, `shared_only`, `start`, `with_fields` |
| `POST` | `/file_search/check_reindex` | `searchCheckReindex` | `form-urlencoded` | - |
| `POST` | `/file_search/get_setting` | `searchGetSetting` | `form-urlencoded` | - |
| `POST` | `/file_search/save_setting` | `searchSaveSetting` | `form-urlencoded` | `cycle_period`, `skip_standby`, `time_point` |
| `POST` | `/file_search/reindex` | `searchReindex` | `form-urlencoded` | - |
| `POST` | `/v2/file/rclean` | `rdelall` | `form-urlencoded` | - |
| `POST` | `/v2/public/recycle/clean` | `cleanPubRecycle` | `form-urlencoded` | - |
| `POST` | `/v2/recent/remove` | `removeRecentMark` | `form-urlencoded` | - |
| `POST` | `/v2/file/latest/remove` | `removeLastestMark` | `form-urlencoded` | - |
| `POST` | `/v2/file/restore` | `restore` | `form-urlencoded` | - |
| `POST` | `/v2/public/recycle/restore` | `pubRestore` | `form-urlencoded` | - |
| `POST` | `/v2/file/mrestore` | `mrestore` | `form-urlencoded` | - |
| `POST` | `/v2/public/permission/switch` | `setPublicSwitch` | `form-urlencoded` | `opt` |
| `POST` | `/v2/public/permission/get` | `getPublicSwitch` | `form-urlencoded` | `dir_name` |
| `POST` | `/v2/public/permission/set` | `setPermissions` | `form-urlencoded` | `param` |
| `POST` | `/v2/public/permission/list` | `getPermissions` | `form-urlencoded` | `dir_name` |
| `POST` | `/v2/public/group/list` | `getGroupList` | `form-urlencoded` | `num`, `order`, `path`, `sortby`, `start`, `with_fields` |
| `POST` | `/v2/public/group/add` | `addGroup` | `form-urlencoded` | `dir_name`, `location` |
| `POST` | `/v2/public/group/delete` | `delGroup` | `form-urlencoded` | - |
| `POST` | `/zconn/http/5055/{{t}}` | `getRemoteFileList` | `form-urlencoded` | - |
| `POST` | `/v2/file/list` | `getFileList` | `form-urlencoded` | `driveId`, `folderId`, `ftype`, `loadMore`, `num`, `order`, `path`, `sortby`, `start` |
| `POST` | `/v2/file/list/stream` | `getFileListStream` | `form-urlencoded` | - |
| `POST` | `/v2/file/categories` | `categories` | `form-urlencoded` | - |
| `POST` | `/v2/file/newdir` | `newdir` | `form-urlencoded` | `name`, `parent`, `rename` |
| `POST` | `/v2/file/modify` | `modify` | `form-urlencoded` | `dwstate`, `newname`, `path` |
| `POST` | `/v2/onlyoffice/file/rename` | `modifyOnlyofficeFile` | `form-urlencoded` | - |
| `POST` | `/v2/file/adddw` | `addDwDir` | `form-urlencoded` | `dwstate`, `paths` |
| `POST` | `/v2/file/remove` | `remove` | `form-urlencoded` | `id`, `paths`, `skip_bin` |
| `POST` | `/v2/file/find/large/create` | `createLargeFileScanTask` | `form-urlencoded` | `max_size`, `min_size`, `paths`, `topk` |
| `POST` | `/v2/file/find/large/info` | `getLargeFileScanTaskInfo` | `form-urlencoded` | - |
| `POST` | `/v2/file/find/large/delete` | `deleteLargeFileScanTask` | `form-urlencoded` | - |
| `POST` | `/v2/file/move` | `move` | `form-urlencoded` | `paths`, `to` |
| `POST` | `/v2/file/copy` | `copy` | `form-urlencoded` | - |
| `POST` | `/v2/file/exportlpv` | `exportlpv` | `form-urlencoded` | - |
| `POST` | `/v2/file/tasks` | `tasks` | `form-urlencoded` | `num`, `start`, `state` |
| `POST` | `/v2/file/taskbyids` | `taskbyids` | `form-urlencoded` | `ids` |
| `POST` | `/v2/file/taskdel` | `taskdel` | `form-urlencoded` | `id` |
| `POST` | `/v2/file/task/continue` | `taskContinue` | `form-urlencoded` | `id`, `ignore` |
| `POST` | `/v2/file/task/pause` | `taskPause` | `form-urlencoded` | `id` |
| `POST` | `/v2/nshare/mine` | `myNasShare` | `form-urlencoded` | `num`, `start` |
| `POST` | `/v2/nshare/create` | `createNasShare` | `form-urlencoded` | `path` |
| `POST` | `/v2/nshare/cancel` | `cancelNasShare` | `form-urlencoded` | `path` |
| `POST` | `/v2/decom/create` | `addUncompress` | `form-urlencoded` | - |
| `POST` | `/v2/compression/create` | `addCompress` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/song/file/share` | `createPlaylistFileShare` | `form-urlencoded` | `advanced_mode`, `has_pass`, `share_expire`, `share_type`, `share_value`, `snum` |
| `POST` | `/setting/load` | `createCollectionShare` | `form-urlencoded` | `advanced_mode`, `classification_id`, `collection_id`, `expire`, `has_pass`, `snum` |
| `POST` | `/zvideo/share/collection/create` | `createCollectionShare` | `form-urlencoded` | `advanced_mode`, `classification_id`, `collection_id`, `expire`, `has_pass`, `snum` |
| `POST` | `/v2/share/create` | `createShareLink` | `form-urlencoded` | `expire`, `has_pass`, `paths`, `type` |
| `POST` | `/redirect/v2/share/token` | `getShareToken` | `form-urlencoded` | - |
| `POST` | `https://{{s}}` | `getShareToken` | `form-urlencoded (direct)` | - |
| `POST` | `/redirect/v2/share/files` | `getShareList` | `form-urlencoded` | `num`, `size`, `start`, `type` |
| `POST` | `/v2/recent/new` | `reportVisit` | `form-urlencoded` | `path` |
| `POST` | `/v2/recent/list` | `recentlyVisited` | `form-urlencoded` | `num`, `scope`, `start`, `with_fields` |
| `POST` | `/v2/file/latest/list` | `recentlyBackuped` | `form-urlencoded` | `num`, `start` |
| `POST` | `/v2/file/hash` | `hash` | `form-urlencoded` | `paths` |
| `POST` | `/v2/file/sysdir` | `sysdir` | `form-urlencoded` | - |
| `POST` | `/v2/file/upload/whyfail` | `uploadWhyFail` | `form-urlencoded` | - |
| `GET` | `/v2/file/tmpinfo` | `uploadTmpInfo` | `query` | `path`, `uuid` |
| `POST` | `/v2/file/statistic/passthrough` | `statistic` | `form-urlencoded` | `paths`, `show_hidden` |
| `POST` | `/v2/file/statistic` | `fileStatistic` | `form-urlencoded` | `paths` |
| `POST` | `/v2/file/statistictask` | `statistictask` | `form-urlencoded` | `id` |
| `POST` | `/v2/weboffic/versiondir` | `webOfficVersionDir` | `form-urlencoded` | `path` |
| `POST` | `/v2/weboffic/getconfig` | `eh` | `form-urlencoded` | - |
| `POST` | `/v2/weboffic/saveconfig` | `ef` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/info` | `getFileShareInfo` | `form-urlencoded` | `path` |
| `POST` | `/v2/encryptdir/new` | `encryptdirNew` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/make` | `makeShareToUser` | `form-urlencoded` | - |
| `POST` | `/v2/encryptdir/unlock` | `encryptdirUnlock` | `form-urlencoded` | `password`, `path` |
| `POST` | `/v2/nshare/discard` | `discardShare` | `form-urlencoded` | `path` |
| `POST` | `/v2/encryptdir/lock` | `encryptdirLock` | `form-urlencoded` | `path` |
| `POST` | `/v2/nshare/forme` | `allShareToMe` | `form-urlencoded` | `order`, `sortby` |
| `POST` | `/v2/encryptdir/release` | `encryptdirRelease` | `form-urlencoded` | `password`, `path` |
| `POST` | `/v2/nshare/list` | `allMyShareList` | `form-urlencoded` | `order`, `sortby` |
| `POST` | `/v2/encryptdir/resetpasswd` | `encryptdirResetpasswd` | `form-urlencoded` | `password`, `path` |
| `POST` | `/v2/encryptdir/seticon` | `encryptdirSeticon` | `form-urlencoded` | `encrypt_icon`, `path` |
| `POST` | `/v2/encryptdir/rename` | `encryptRename` | `form-urlencoded` | `newname`, `path` |
| `POST` | `/v2/file/cross/info` | `crossInfo` | `form-urlencoded` | `path` |
| `GET` | `/flashmsg` | `flashmsg` | `query` | - |
| `POST` | `/file_search/slow_file_search/start` | `slowFileSearchStart` | `form-urlencoded` | - |
| `POST` | `/file_search/slow_file_search/get_result` | `slowFileSearchStartGetResult` | `form-urlencoded` | `num`, `start`, `taskid` |
| `POST` | `/file_search/slow_file_search/stop` | `slowFileSearchStartStop` | `form-urlencoded` | `taskid` |
| `POST` | `/v2/file/listext/nameuuid` | `getFilesExtWidthName` | `form-urlencoded` | `num`, `path`, `start` |
| `POST` | `/v2/compression/browser` | `compressionBrowser` | `form-urlencoded` | - |
| `POST` | `/v2/compression/download` | `compressionDownload` | `form-urlencoded` | - |
| `POST` | `/v2/file/recycle/config/get` | `recycleConfigGet` | `form-urlencoded` | - |
| `POST` | `/v2/file/recycle/config/save` | `recycleConfigSave` | `form-urlencoded` | - |
| `POST` | `/v2/file/note/set` | `setFileNote` | `form-urlencoded` | `file_note`, `filepaths` |
| `POST` | `/transcode/video/process/add` | `addVideoTranscode` | `form-urlencoded` | `file_path` |
| `POST` | `/transcode/video/process/query` | `queryVideoTranscode` | `form-urlencoded` | `file_path` |
| `POST` | `/file_search/office_search` | `postOfficeSearch` | `form-urlencoded` | - |
| `POST` | `/file_search/set_content_dir` | `postSetContentDir` | `form-urlencoded` | - |
| `POST` | `/file_search/get_fail_files` | `postGetFailFiles` | `form-urlencoded` | `path` |
| `POST` | `/file_search/reindex_content` | `postReindexContent` | `form-urlencoded` | - |
| `POST` | `/file_search/slow_office_search/start` | `postSlowOfficeSearchStart` | `form-urlencoded` | - |
| `POST` | `/file_search/slow_office_search/get_result` | `postSlowOfficeSearchGetResult` | `form-urlencoded` | `num`, `start`, `taskid` |
| `POST` | `/file_search/slow_office_search/stop` | `postSlowOfficeSearchStop` | `form-urlencoded` | `taskid` |
| `POST` | `/file_search/get_content_dirs` | `postGetContentDirs` | `form-urlencoded` | - |
| `POST` | `/v2/file/safe/status` | `getBoxStatus` | `form-urlencoded` | `safe_dir` |
| `POST` | `/v2/file/safe/init` | `openBox` | `form-urlencoded` | - |
| `POST` | `/v2/file/safe/tips` | `getTips` | `form-urlencoded` | - |
| `POST` | `/v2/file/safe/open` | `open` | `form-urlencoded` | `password`, `safe_dir` |
| `POST` | `/v2/file/safe/reset` | `reset` | `form-urlencoded` | `pwd` |
| `POST` | `/v2/file/safe/close` | `close` | `form-urlencoded` | - |
| `POST` | `/v2/file/safe/dwstatus` | `getStatus` | `form-urlencoded` | - |
| `POST` | `/v2/labels/createfilelabel` | `createfilelabel` | `form-urlencoded` | `filepaths`, `label_name` |
| `POST` | `/v2/labels/alllabels` | `alllabels` | `form-urlencoded` | `limit` |
| `POST` | `/v2/labels/deletelabel` | `deletelabel` | `form-urlencoded` | `label_names` |
| `POST` | `/v2/labels/savefilelabel` | `savefilelabel` | `form-urlencoded` | `filepaths`, `label_names` |
| `POST` | `/v2/labels/deletefilelabel` | `deletefilelabel` | `form-urlencoded` | `filepaths`, `label_name` |
| `POST` | `/v2/labels/getfiles` | `getfiles` | `form-urlencoded` | - |
| `POST` | `/v2/labels/rename` | `rename` | `form-urlencoded` | - |
| `POST` | `/v2/labels/renamestatus` | `renamestatus` | `form-urlencoded` | `task_id` |
| `POST` | `/v2/labels/savelabelorder` | `savelabelorde` | `form-urlencoded` | `labels`, `top_labels` |
| `POST` | `/v2/fdd/state` | `scanResult` | `form-urlencoded` | - |
| `POST` | `/v2/fdd/create` | `scan` | `form-urlencoded` | - |
| `POST` | `/v2/fdd/list` | `allScanList` | `form-urlencoded` | `ftype`, `num`, `start` |
| `POST` | `/v2/fdd/glist` | `oneGroupList` | `form-urlencoded` | `id`, `num`, `start` |
| `POST` | `/v2/fdd/clean` | `clean` | `form-urlencoded` | `batch_type`, `ftype`, `recycle`, `sgf_ids`, `with_live_video` |
| `POST` | `/v2/fdd/modify` | `changeState` | `form-urlencoded` | `state` |
| `POST` | `/v2/file/notepad/list` | `getNoteList` | `form-urlencoded` | `classify_id`, `label_id`, `location`, `note_type`, `num`, `start` |
| `POST` | `/v2/file/notepad/new` | `create` | `form-urlencoded` | `amdModuleId`, `body`, `classify_id`, `esmModuleLocation`, `in_brief`, `iv`, `key`, `label`, `location`, `note_type`, `title`, `type` |
| `POST` | `/v2/file/notepad/info` | `info` | `form-urlencoded` | `id`, `location` |
| `POST` | `/v2/file/notepad/modify` | `save` | `form-urlencoded` | `body`, `id`, `in_brief`, `location`, `note_type`, `title` |
| `GET` | `/v2/file/notepad/downloadt` | `download` | `query` | - |
| `POST` | `/v2/file/notepad/delete` | `del` | `form-urlencoded` | `ids`, `location` |
| `POST` | `/v2/file/notepad/newclassify` | `addClass` | `form-urlencoded` | `location`, `name`, `parent_id` |
| `POST` | `/v2/file/notepad/classifylist` | `getClassList` | `form-urlencoded` | - |
| `POST` | `/v2/file/notepad/deleteclassify` | `delClass` | `form-urlencoded` | `id`, `location` |
| `POST` | `/v2/file/notepad/updateclassify` | `modClass` | `form-urlencoded` | `id`, `location`, `name`, `parent_id` |
| `POST` | `/v2/file/notepad/movenotepad` | `moveNoteToClass` | `form-urlencoded` | `classify_id`, `ids`, `location` |
| `POST` | `/v2/file/notepad/updatelabel` | `markNote` | `form-urlencoded` | `action`, `id`, `label_id`, `location` |
| `POST` | `/v2/file/notepad/searchnotepad` | `searchNote` | `form-urlencoded` | - |
| `POST` | `/v2/file/notepad/setconfig` | `setConfig` | `form-urlencoded` | `config_key`, `config_value`, `location`, `progressDot`, `scope` |
| `POST` | `/v2/file/notepad/getconfig` | `getConfig` | `form-urlencoded` | `config_key`, `location`, `scope` |
| `POST` | `/v2/file/notepad/totalsize` | `getTotalSize` | `form-urlencoded` | `location` |
| `POST` | `/v2/file/notepad/historylist` | `historyList` | `form-urlencoded` | `location`, `notepad_id`, `num`, `start` |
| `POST` | `/v2/file/notepad/historyinfo` | `historyItem` | `form-urlencoded` | `id`, `location` |
| `POST` | `/v2/file/notepad/allclassify` | `getAllClasses` | `form-urlencoded` | `location` |
| `POST` | `/v2/file/notepad/save_classify_tree` | `saveClassifySort` | `form-urlencoded` | `classify_tree`, `location` |
| `POST` | `/v2/file/notepad/pin` | `pin` | `form-urlencoded` | `cancel`, `id`, `location` |
| `POST` | `/v2/nshare/review/project/create` | `i` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/project/list` | `a` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/video/add` | `s` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/transcode/task/list` | `c` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/transcode/progress/list` | `u` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/transcode/task/cancel` | `l` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/transcode/task/resume` | `d` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/share/modifybyproject` | `m` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/share/deletebyproject` | `h` | `form-urlencoded` | - |
| `POST` | `/v2/nshare/review/project/delete` | `f` | `form-urlencoded` | - |
| `POST` | `/v2/receiver/create` | `postReceiverCreate` | `form-urlencoded` | - |
| `POST` | `/v2/receiver/list` | `getReceiverList` | `form-urlencoded` | - |
| `POST` | `/v2/receiver/action` | `postReceiverAction` | `form-urlencoded` | `action`, `ids` |
| `POST` | `/v2/receiver/info` | `postReceiverInfo` | `form-urlencoded` | `id` |
| `POST` | `/v2/receiver/files` | `postReceiverFiles` | `form-urlencoded` | `id` |
| `POST` | `/file/nasshare` | `default` | `form-urlencoded` | `node`, `option` |
| `POST` | `/file/mynassharelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |

### 4.2 存储池 (ZSPool / StoragePool)、硬盘 SMART、快照、iSCSI 与文件共享服务 (共 257 个端点)
涵盖 Webpack 模块 `83787` (ZSPool 存储池/RAID/SMART/碎片/缓存/均衡)、`52324` (传统 StoragePool、Samba/NFS/FTP/WebDAV/SFTP/DLNA 文件共享服务、系统进程与服务管理)、`89794` (ZSPool 快照 Snapshot)、`67420` (虚拟 U 盘 vUSB)、`41081` & `83579` (eSATA 外置存储管理)、`62434` (iSCSI SAN 存储管理 `/v1/zsanmanager/*`)。

> **补充 Electron 壳层 (`app.d3d20628.js`) 专有 S3 挂载接口**：
> - `POST /api/fileshare_service/minio/enable`：启用 MinIO S3 服务（参数 `type: 4`, `data: JSON`）
> - `POST /api/fileshare_service/minio/get_s3_key`：获取本地挂载所需的 S3 AccessKey / SecretKey

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/zspool/polling` | `polling2` | `form-urlencoded` | - |
| `POST` | `/zspool/format/pending` | `formatStartFromSimpleMode` | `form-urlencoded` | `id` |
| `POST` | `/zspool/format/exec` | `formatStartFromHighLvMode` | `form-urlencoded` | - |
| `POST` | `/zspool/info` | `getPoolInfo` | `form-urlencoded` | - |
| `POST` | `/zspool/format/done` | `shutFormatDoneStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/hardware/info` | `getSlotInfo` | `form-urlencoded` | - |
| `POST` | `/zspool/cache/delete` | `deleteCache` | `form-urlencoded` | `pid` |
| `POST` | `/zspool/pool/delete` | `deleteStorage` | `form-urlencoded` | `pid` |
| `POST` | `/zspool/smart/report2` | `smartDetail` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/smart/test/start` | `startSmart` | `form-urlencoded` | `pid` |
| `POST` | `/zspool/smart/test/status	` | `smartStatus` | `form-urlencoded` | `pid` |
| `POST` | `/zspool/ihm/check/status` | `IHMStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/ihm/report/view` | `IHMDetail` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/ihm/check/start	` | `startIHM` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/smr/alert/ignore` | `smrAlertIgnore` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/balance/flush/start` | `balanceFlushStart` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/balance/flush/status` | `balanceFlushStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/balance/task/start` | `balanceTaskStart` | `form-urlencoded` | `gids`, `pid`, `pos`, `type` |
| `POST` | `/zspool/balance/task/timer` | `balanceTaskTimer` | `form-urlencoded` | - |
| `POST` | `/zspool/balance/task/status` | `balanceTaskStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/balance/task/cancel` | `balanceTaskCancel` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/auth/pool/perms` | `setUserStoragePerm` | `form-urlencoded` | `ids`, `perms`, `pool` |
| `POST` | `/auth/user/list` | `getUserStoragePerm` | `form-urlencoded` | `all`, `cq` |
| `POST` | `/auth/pool/quota` | `setUserQuota` | `form-urlencoded` | `id`, `pool`, `quota` |
| `POST` | `/v2/public/quota/set` | `setPublicQuota` | `form-urlencoded` | `path`, `quota` |
| `POST` | `/v2/public/quota/list` | `getPublicQuotaList` | `form-urlencoded` | `paths` |
| `POST` | `/system/diskusage/status` | `getDiskUsageStatus` | `form-urlencoded` | - |
| `POST` | `/system/diskusage/runanyway` | `diskUsageRun` | `form-urlencoded` | - |
| `POST` | `/zspool/scrub/task/status` | `getRaid5CleanStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/scrub/task/start` | `startStorageCleanTask` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/scrub/task/cancel` | `scrubCancel` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/zspool/format/cancel` | `cancelFormat` | `form-urlencoded` | - |
| `POST` | `/zspool/message/delete` | `deletePollingMessage` | `form-urlencoded` | - |
| `POST` | `/storagepool/spupdate/v3` | `upgrade` | `form-urlencoded` | - |
| `POST` | `/zspool/pool/mount` | `forceMountPool` | `form-urlencoded` | `action`, `del`, `pid` |
| `POST` | `/zspool/external/fsmsg` | `extdevStorageSetting` | `form-urlencoded` | `filesearch`, `fileupdate`, `ptuuid`, `sn`, `thumbnails` |
| `POST` | `/zspool/external/smart/report2` | `getExtSmartResult` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/external/smart/test/start` | `startExtSmart` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/external/smart/test/status` | `getExtSmartStatus` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/external/balance/flush/start` | `extBalanceFlushStart` | `form-urlencoded` | `ptuuid`, `sn` |
| `POST` | `/zspool/external/balance/task/start` | `extBalanceTaskStart` | `form-urlencoded` | `ptuuid`, `sn` |
| `POST` | `/zspool/external/balance/task/status` | `extBalanceTaskStatus` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/external/balance/task/cancel` | `extBalanceTaskCancel` | `form-urlencoded` | `ptuuid`, `sn` |
| `POST` | `/zspool/external/balance/reset` | `extBalanceTaskReset` | `form-urlencoded` | `ptuuid`, `sn` |
| `POST` | `/zspool/get/pwdprompt` | `getEncryptStoragePasswordTips` | `form-urlencoded` | - |
| `POST` | `/zspool/pwdfile/upload` | `uploadKey` | `multipart/form-data` | - |
| `POST` | `/zspool/decrypted/pool` | `unlock` | `form-urlencoded` | `encryptpwd` |
| `GET` | `/zspool/pwdfile/download` | `downloadEncryptStorageKey` | `application/octet-stream` | `encryptpwd` |
| `POST` | `/zspool/esata/set/version` | `esataVersionChange` | `form-urlencoded` | `ever` |
| `POST` | `/zspool/capability` | `supportEncryptedPool` | `form-urlencoded` | - |
| `POST` | `/zspool/cache/hits` | `cacheHits` | `form-urlencoded` | `pid` |
| `POST` | `/zspool/smart/task/set` | `smartTaskSet` | `form-urlencoded` | - |
| `POST` | `/zspool/smart/task/get` | `smartTaskGet` | `form-urlencoded` | `pid` |
| `GET` | `/healthcheck/syncthing` | `checkSyncthing` | `query` | - |
| `POST` | `/api/fileshare_service/samba/mapping/list` | `n` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/samba/mapping/rename` | `r` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/samba/mapping/del` | `c` | `form-urlencoded` | - |
| `POST` | `/system/polling2` | `systemPoll` | `form-urlencoded` | - |
| `POST` | `/system/recent` | `monitor` | `form-urlencoded` | - |
| `POST` | `/system/delay` | `monitorDelay` | `form-urlencoded` | - |
| `POST` | `/manager/v2/processes` | `getAllProcesses` | `form-urlencoded` | - |
| `POST` | `/manager/srv/start` | `startService` | `form-urlencoded` | `command` |
| `POST` | `/manager/srv/stop` | `stopService` | `form-urlencoded` | `command` |
| `GET` | `/hdmiapi/getlogin` | `hdmiapi_getlogin` | `query` | - |
| `GET` | `/hdmiapi/getfontlist` | `v` | `query` | - |
| `POST` | `/hdmiapi/setfont` | `b` | `form-urlencoded` | - |
| `POST` | `/manager/services` | `getServiceList` | `form-urlencoded` | - |
| `POST` | `/mysql/repair` | `mysqlRepair` | `form-urlencoded` | - |
| `POST` | `/mysql/state` | `mysqlState` | `form-urlencoded` | - |
| `POST` | `/system` | `systemInfo` | `form-urlencoded` | - |
| `POST` | `/system/shutdown` | `shutdown` | `form-urlencoded` | - |
| `POST` | `/system/reboot` | `reboot` | `form-urlencoded` | - |
| `POST` | `/zconn/http/5055/{{t}}` | `remoteSystemPoll` | `form-urlencoded` | - |
| `GET` | `/zconn/http/5055/{{t}}` | `getRemoteBackupNasStat` | `query` | - |
| `POST` | `/disk/format` | `format` | `form-urlencoded` | `id` |
| `POST` | `/disk/mounts` | `mounts` | `form-urlencoded` | - |
| `POST` | `/system/home` | `home` | `form-urlencoded` | - |
| `POST` | `/auth/master` | `checkedDev` | `form-urlencoded` | - |
| `POST` | `/migrate/diskready` | `checkDisk` | `form-urlencoded` | - |
| `POST` | `/migrate/ckusername` | `checkUsername` | `form-urlencoded` | `username` |
| `POST` | `/migrate/state` | `askState` | `form-urlencoded` | - |
| `POST` | `/migrate/start` | `startChangeDev` | `form-urlencoded` | `sms_proof` |
| `POST` | `/migrate/start/by/username` | `startChangeByUName` | `form-urlencoded` | `username` |
| `POST` | `/migrate/cleanstate` | `clearnState` | `form-urlencoded` | - |
| `POST` | `/disk/confirm` | `confirmForLostDisk` | `form-urlencoded` | - |
| `GET` | `/upgrade/progress` | `upgradeProcess` | `query` | - |
| `POST` | `/upgrade/retry` | `upgradeRetry` | `form-urlencoded` | `app_id`, `check_license`, `id` |
| `POST` | `/storagepool/format/option` | `formatStart` | `form-urlencoded` | `id`, `password`, `pwd`, `pwdTips`, `username` |
| `POST` | `/storagepool/info2` | `getFormatDiskResult` | `form-urlencoded` | - |
| `POST` | `/storagepool/reset` | `resetPool` | `form-urlencoded` | `password`, `username` |
| `POST` | `/storagepool/confirm` | `confirm` | `form-urlencoded` | `dev`, `id`, `option` |
| `POST` | `/storagepool/dev/remove` | `removeSata` | `form-urlencoded` | `dev` |
| `POST` | `/storagepool/dev/add` | `addSata` | `form-urlencoded` | `dev` |
| `POST` | `/storagepool/format/ssd` | `formatSSD` | `form-urlencoded` | `dev`, `password`, `username` |
| `POST` | `/storagepool/suggestion` | `getSuggestion` | `form-urlencoded` | - |
| `POST` | `/storagepool/sync/status` | `getSyncStatus` | `form-urlencoded` | - |
| `POST` | `/v2/file/startdw` | `startDw` | `form-urlencoded` | - |
| `POST` | `/v2/file/safe/startdw` | `startSafeDw` | `form-urlencoded` | - |
| `POST` | `/storagepool/smart/selflist` | `smartList` | `form-urlencoded` | - |
| `POST` | `/storagepool/smart/report/view` | `smartDetail` | `form-urlencoded` | `sn` |
| `GET` | `/storagepool/smart/start` | `startSmart` | `query` | `pid` |
| `GET` | `/storagepool/smart/status` | `smartStatus` | `query` | `pid` |
| `POST` | `/storagepool/ihm/check/start` | `startIHM` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/storagepool/ihm/report/view` | `IHMDetail` | `form-urlencoded` | `sn` |
| `POST` | `/storagepool/ihm/check/status` | `IHMStatus` | `form-urlencoded` | `pid`, `type` |
| `GET` | `/storagepool/sata/devs` | `allDisksInfo` | `query` | - |
| `POST` | `/zspool/hddmove/replace/select` | `switchSelect` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/hddmove/replace/statistics` | `switchStatistics` | `form-urlencoded` | `pid`, `sn` |
| `GET` | `/zspool/hddmove/replace/status` | `switchStatus` | `query` | - |
| `POST` | `/zspool/hddmove/replace/create` | `switchStart` | `form-urlencoded` | `sn` |
| `GET` | `/zspool/hddmove/replace/cancel` | `switchCancel` | `query` | - |
| `POST` | `/zspool/hddmove/replace/execute` | `switchExecute` | `form-urlencoded` | `savefail` |
| `POST` | `/zspool/hddmove/replace/usb/scan` | `switchUsbScan` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/replace/usb/status` | `switchUsbStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/replace/continue` | `switchContinue` | `form-urlencoded` | `savefail` |
| `POST` | `/zspool/hddmove/detach/check` | `migrationCheck` | `form-urlencoded` | `pid`, `sn` |
| `POST` | `/zspool/hddmove/detach/start` | `migrationStart` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/detach/execute` | `migrationExecute` | `form-urlencoded` | `savefail` |
| `POST` | `/zspool/hddmove/detach/status` | `migrationStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/detach/cancel` | `migrationCancel` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/detach/usb/scan` | `migrationUsbScan` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/detach/usb/status` | `migrationUsbStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/detach/continue` | `migrationContinue` | `form-urlencoded` | `savefail` |
| `POST` | `/zspool/hddmove/emove/status` | `esataMoveStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/emove/prepare` | `esataMovePrepare` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/hddmove/emove/retry` | `esataMoveRetry` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/emove/next` | `esataMoveNext` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/emove/cancel` | `esataMoveCancel` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/emove/fumount` | `esataMoveFumount` | `form-urlencoded` | - |
| `POST` | `/zspool/hddmove/emove/exec` | `esataMoveExec` | `form-urlencoded` | - |
| `POST` | `"/zspool/external/info")` | `startDiskWipe` | `form-urlencoded` | `repeats`, `sn` |
| `POST` | `/zspool/external/wipe/start` | `stopDiskWipe` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/external/wipe/cancel` | `createDiskFormat` | `form-urlencoded` | `dev`, `fs`, `label`, `uuid` |
| `GET` | `/storagepool/dev/format/clear` | `closeDiskFormat` | `query` | - |
| `GET` | `"/zspool/format/external/exec")` | `retryDiskFormat` | `query` | - |
| `GET` | `/upgrade/license/check` | `getUpgradeLicenseCheck` | `query` | - |
| `GET` | `/upgrade/license/agree` | `getUpgradeLicenseAgree` | `query` | `has_agreed_license` |
| `POST` | `/action/continue` | `consumeMessage` | `form-urlencoded` | `id` |
| `POST` | `/api/fileshare_service` | `sftpAndNfs` | `form-urlencoded` | - |
| `GET` | `/storagepool/scrub/status` | `getRaid5CleanStatus` | `query` | `pid`, `type` |
| `POST` | `/storagepool/scrub/timer` | `setTimerTask` | `form-urlencoded` | - |
| `GET` | `/storagepool/scrub/start` | `startStorageCleanTask` | `query` | `pid`, `type` |
| `GET` | `/storagepool/scrub/cancel` | `cancelStorageCleanTask` | `query` | - |
| `GET` | `/storagepool/ssd/suggestion` | `ssdSuggestion` | `query` | - |
| `POST` | `/storagepool/ssd/reset` | `changeSsdStyle` | `form-urlencoded` | `password`, `username` |
| `POST` | `/manager/srvs` | `getAllServices` | `form-urlencoded` | - |
| `POST` | `/storagepool/conf/backup/recovery` | `recoverStoragePool` | `form-urlencoded` | - |
| `POST` | `/storagepool/ssd/enable` | `useSSD` | `form-urlencoded` | - |
| `POST` | `/storagepool/ssd/format/cache` | `ssdCacheMode` | `form-urlencoded` | `cache`, `id`, `sn` |
| `POST` | `/storagepool/ssd/format/storage` | `ssdStorageMode` | `form-urlencoded` | `id`, `sn` |
| `POST` | `/storagepool/ssd/pending/ignore` | `ignoreSSD` | `form-urlencoded` | `id` |
| `GET` | `/storagepool/format/whyfail` | `getFormatFailMessage` | `query` | - |
| `POST` | `/storagepool/ssd/wcache/fmount` | `wcacheForceMount` | `form-urlencoded` | - |
| `POST` | `/storagepool/ssd/wcache/finish` | `cancelCacheFlushFinish` | `form-urlencoded` | - |
| `GET` | `/upgrade/info` | `getUpgradeInfo` | `query` | - |
| `GET` | `/upgrade/config` | `upgradeInfoChange` | `query` | `auto_upgrade` |
| `POST` | `/upgrade/confirm` | `upgradeConfirm` | `form-urlencoded` | `mod` |
| `POST` | `/upgrade/retry_system` | `retrySystem` | `form-urlencoded` | - |
| `POST` | `/upgrade/retry_service` | `retryService` | `form-urlencoded` | - |
| `POST` | `/upgrade/skipversion/add` | `skipVersionAdd` | `application/json` | - |
| `GET` | `/upgrade/skipversion/list` | `getSkipVersion` | `query` | - |
| `POST` | `/upgrade/skipversion/remove` | `cleanSkipVersion` | `form-urlencoded` | - |
| `POST` | `/system/diskusage3` | `diskUsedInfo` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/dlna/status` | `dlnaStatus` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/dlna/enable` | `dlnaSwitch` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/dlna/dir` | `dlnaDir` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/samba/recycle` | `extdevRecycle` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/webdav/status` | `webdavStatus` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/webdav/enable` | `webdavSwitch` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/webdav/config` | `webdavPort` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/samba/status` | `smbStatus` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/samba/enable` | `smbSwitch` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/samba/config` | `smbSetting` | `form-urlencoded` | `tyoe` |
| `POST` | `/api/fileshare_service/samba/config/advanced` | `smbSettingNtlm` | `form-urlencoded` | `type` |
| `GET` | `/storagepool/esata/smart/start` | `startEsataSmart` | `query` | - |
| `GET` | `/storagepool/esata/smart/status` | `esataSmartStatus` | `query` | - |
| `POST` | `/api/fileshare_service/ftp/status` | `ftpStatus` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/ftp/enable` | `ftpSwitch` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/ftp/config/guest` | `ftpGuest` | `form-urlencoded` | - |
| `GET` | `/resetSyncthingDB` | `resetSyncthingDB` | `query` | - |
| `POST` | `/app/notify` | `appNotify` | `form-urlencoded (server)` | `app`, `np`, `plat`, `version` |
| `GET` | `/hdmiapi/logout` | `hdmiapi_logout` | `query` | - |
| `POST` | `/manager/srv/blue/clean/rt` | `cleanBTConn` | `form-urlencoded` | - |
| `GET` | `/hdmiapi/setWallpaper` | `setWallpaper` | `query` | `current`, `path`, `paths` |
| `POST` | `/auth/avatar/save` | `authAvatarSave` | `form-urlencoded` | `avatar` |
| `GET` | `/AppStore/backupNasApi/getServiceStat` | `getBackupNasStat` | `query` | - |
| `GET` | `/AppStore/backupNasApi/setServiceStat` | `setBackupNasStat` | `query` | `enable` |
| `GET` | `/hdmidesktop/start` | `hdmidesktopStart` | `query` | - |
| `GET` | `/hdmidesktop/stop` | `hdmidesktopStop` | `query` | - |
| `GET` | `/hdmidesktop/status` | `hdmidesktopStatus` | `query` | - |
| `POST` | `/auth/oauth2/authorize` | `oauthAuthorize` | `form-urlencoded` | `os`, `redirectUrl`, `source` |
| `POST` | `/zspool/snapshot/task/get` | `o` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/init` | `s` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/task/set/on` | `a` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/task/set/off` | `r` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/task/set/plan` | `c` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/task/set/rule` | `l` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/create` | `u` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/delete` | `h` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/edit` | `d` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/restore` | `p` | `form-urlencoded` | - |
| `POST` | `/zspool/snapshot/list` | `m` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/status` | `vusbStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/create` | `vusbCreate` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/dilate` | `vusbDilate` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/on` | `vusbOn` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/off` | `vusbOff` | `form-urlencoded` | `reboot` |
| `POST` | `/zspool/vusb/cancel` | `vusbCancel` | `form-urlencoded` | - |
| `POST` | `/zspool/vusb/reset` | `vusbReset` | `form-urlencoded` | - |
| `POST` | `e?"/zspool/vusb/mobile/on":"/zspool/vusb/mobile/off"` | `changeMobileOn` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/confirm` | `esataConfirm` | `form-urlencoded` | `id`, `option` |
| `POST` | `/zspool/esata/pending/ignore` | `cancelPending` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/format/option` | `format` | `form-urlencoded` | `id` |
| `POST` | `/zspool/esata/format/cancel` | `cancelFormat` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/sys/reset` | `resetEsata` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/sys/shutdown` | `uninstall` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/sys/status` | `uninstallStatus` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/sys/remove` | `removeEsata` | `form-urlencoded` | - |
| `POST` | `/zspool/esata/enable/idevs` | `useEsataDisk` | `form-urlencoded` | `sn` |
| `POST` | `/zspool/esata/sys/reboot` | `rebootEsata` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/confirm` | `esataConfirm` | `form-urlencoded` | `id`, `option` |
| `POST` | `/storagepool/esata/pending/ignore` | `cancelPending` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/format/option` | `format` | `form-urlencoded` | `id` |
| `POST` | `/storagepool/esata/format/cancel` | `cancelFormat` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/v2/sys/reset` | `resetEsata` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/sys/shutdown` | `uninstall` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/sys/status` | `uninstallStatus` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/v2/sys/remove` | `removeEsata` | `form-urlencoded` | - |
| `POST` | `/storagepool/esata/enable/idevs` | `useEsataDisk` | `form-urlencoded` | `sn` |
| `POST` | `/storagepool/esata/v2/sys/reboot` | `rebootEsata` | `form-urlencoded` | - |
| `GET` | `/v1/zsanmanager/sessions` | `sessions` | `query` | - |
| `GET` | `/v1/zsanmanager/info` | `info` | `query` | `id`, `location` |
| `GET` | `/v1/zsanmanager/storageList` | `storageList` | `query` | `all` |
| `GET` | `/v1/zsanmanager/lun/listBySn` | `lunListBySn` | `query` | - |
| `GET` | `/v1/zsanmanager/lun/list` | `lunList` | `query` | - |
| `GET` | `/v1/zsanmanager/lun/info` | `lunInfo` | `query` | `uuid` |
| `GET` | `/v1/zsanmanager/lun/preinfo` | `lunPreinfo` | `query` | - |
| `POST` | `/v1/zsanmanager/lun/create` | `lunCreate` | `application/json` | - |
| `GET` | `/v1/zsanmanager/lun/delete` | `lunDelete` | `query` | - |
| `POST` | `/v1/zsanmanager/lun/edit` | `lunEdit` | `application/json` | `uuid` |
| `GET` | `/v1/zsanmanager/target/list` | `targetList` | `query` | - |
| `GET` | `/v1/zsanmanager/target/info` | `targetInfo` | `query` | `uuid` |
| `GET` | `/v1/zsanmanager/target/enable` | `targetEnable` | `query` | `uuid` |
| `GET` | `/v1/zsanmanager/target/disable` | `targetDisable` | `query` | `uuid` |
| `GET` | `/v1/zsanmanager/target/preinfo` | `targetPreinfo` | `query` | - |
| `POST` | `/v1/zsanmanager/target/create` | `targetCreate` | `application/json` | - |
| `GET` | `/v1/zsanmanager/target/delete` | `targetDelete` | `query` | `uuid` |
| `POST` | `/v1/zsanmanager/target/edit` | `targetEdit` | `application/json` | `uuid` |
| `GET` | `/v1/zsanmanager/group/list` | `groupList` | `query` | - |
| `GET` | `/v1/zsanmanager/group/info` | `groupInfo` | `query` | - |
| `GET` | `/v1/zsanmanager/group/preinfo` | `groupPreinfo` | `query` | - |
| `POST` | `/v1/zsanmanager/group/create` | `groupCreate` | `application/json` | - |
| `GET` | `/v1/zsanmanager/group/delete` | `groupDelete` | `query` | `uuid` |
| `POST` | `/v1/zsanmanager/group/edit` | `groupEdit` | `application/json` | `uuid` |
| `GET` | `/v1/zsanmanager/setting/info` | `settingInfo` | `query` | - |
| `POST` | `/v1/zsanmanager/setting/update` | `settingUpdate` | `application/json` | - |
| `GET` | `/v1/zsanmanager/setting/reset` | `settingReset` | `query` | - |

### 4.3 极影视 (ZVideo)、TMDB 刮削、字幕搜索与在线转码播放模块 (共 166 个端点)
涵盖 Webpack 模块 `1297` (极影视分类库管理、海报墙、合集、TMDB 刮削、智能识别、多端共享、字幕搜索与下载 `/subs/*`)、`89194` (极影视电视直播 `/zvideo/live/*`)、`7229` (分享视频播放地址)。

> **补充 Electron 主进程播放器 (`background.js`) 专有转码与播放接口**：
> - `GET /transcode/play` & `GET /transcode/decrypt/play`：获取 HLS/转码播放流
> - `GET /transcode/playaddr` & `GET /transcode/decrypt/playaddr`：获取直连/转码播放地址
> - `POST /transcode/play/stop` & `POST /transcode/decrypt/play/stop` & `POST /play/stop`：停止转码会话
> - `POST /transcode/play/switch` & `POST /transcode/decrypt/play/switch` & `POST /play/switchtrack`：切换音轨/码率
> - `GET /transcode/format` & `GET /transcode/formatmenu`：查询视频转码清晰度菜单
> - `GET /transcode/subtitle` & `GET /transcode/decrypt/subtitle`：获取内嵌/外挂字幕列表
> - `POST /zvideo/video/updateplaytime` & `POST /zvideo/share/updateplaytime`：上报播放进度与字幕设置

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/zvideo/settings/suggested` | `recommendSetting` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/skip` | `skipTitle` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/filter/v2` | `filter_v2` | `form-urlencoded` | - |
| `POST` | `/zvideo/tmdb/settings/load` | `getTMDBSetting` | `form-urlencoded` | - |
| `POST` | `/zvideo/tmdb/validate/key` | `verifyTMDBKey` | `form-urlencoded` | - |
| `POST` | `/zvideo/tmdb/settings` | `setTMDBConfig` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/media` | `l` | `form-urlencoded` | - |
| `POST` | `/subs/v2/searchsub` | `c` | `form-urlencoded` | - |
| `POST` | `/subs/v2/preview` | `f` | `form-urlencoded` | - |
| `POST` | `/subs/v2/downloadsub` | `p` | `form-urlencoded` | - |
| `POST` | `/subs/prematch` | `h` | `form-urlencoded` | - |
| `POST` | `/subs/batchdownload` | `m` | `form-urlencoded` | - |
| `POST` | `/zvideo/settings/load` | `videoSettingsLoad` | `form-urlencoded` | - |
| `POST` | `/zvideo/settings/set` | `videoSettingsSet` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/file/browser` | `z` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/list` | `getSiderBar` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/v2/list` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zvideo/collection/v2/info` | `getInfo` | `form-urlencoded` | `classification_id`, `collection_id`, `get_type` |
| `POST` | `/zvideo/search/cloud` | `cloudSearch` | `form-urlencoded` | - |
| `POST` | `/zvideo/tmdb/search` | `cloudTmdbSearch` | `form-urlencoded` | - |
| `POST` | `/zvideo/tmdb/search/people` | `cloudSearchPeople` | `form-urlencoded` | `name`, `page` |
| `POST` | `/zvideo/search/local` | `nasSearch` | `form-urlencoded` | `classification_id`, `key_word`, `system` |
| `POST` | `/zvideo/classification/increase` | `addContent` | `form-urlencoded` | `classification_id`, `course_id`, `dir_path`, `file_path` |
| `POST` | `/zvideo/collection/correct` | `correctMeta` | `form-urlencoded` | `classification_id`, `collection_id`, `zrid` |
| `POST` | `/zvideo/collection/seen/add` | `look` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/seen/delete` | `unlook` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/like` | `like` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/unlike` | `unlike` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/ruleout` | `deleteEpisode` | `form-urlencoded` | `classification_id`, `collection_id`, `video_id` |
| `POST` | `/zvideo/collection/modify` | `modifyEpisode` | `form-urlencoded` | `classification_id`, `collection_id`, `episode`, `file_path`, `video_id` |
| `POST` | `/zvideo/collection/del` | `deleteCollection` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/classification/del` | `delClassify` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/video/randomlist` | `randomlist` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v2/randomlist` | `randomsharelist` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/add` | `addClassify` | `form-urlencoded` | `classification_name`, `file_path`, `not_scrape`, `share_users` |
| `POST` | `/zvideo/video/updateplaytime` | `updateplaytime` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/unclassifiedlist` | `sysManage` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/auto` | `sysAddClassify` | `form-urlencoded` | - |
| `POST` | `/zvideo/record/info` | `classifyInfo` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/record/list` | `settingList` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/editname` | `changeClassify` | `form-urlencoded` | `classification_id`, `classification_name` |
| `POST` | `/zvideo/classification/rmdir` | `rmDir` | `form-urlencoded` | `classification_id`, `file_path` |
| `POST` | `/zvideo/share/v3/list` | `shareList` | `form-urlencoded` | `field`, `limit`, `start`, `type` |
| `POST` | `/zvideo/scraper/result` | `betterResult` | `form-urlencoded` | `task_id` |
| `POST` | `/zvideo/share/v3/info` | `getShareInfo` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v2/lastplay` | `shareLastplay` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/updateplaytime` | `updateshareplay` | `form-urlencoded` | `collection_id`, `sec`, `video_id` |
| `POST` | `/zvideo/scraper/op` | `better` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/classification/isrefresh` | `isRefresh` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/v2/playlist` | `playRecordList` | `form-urlencoded` | `limit`, `start` |
| `POST` | `/zvideo/record/rlist` | `getRList` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/classification/checkaddstatus` | `isAdding` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/video/v2/lately` | `addList` | `form-urlencoded` | `limit`, `start`, `system` |
| `POST` | `/zvideo/collection/move` | `changeClass` | `form-urlencoded` | `classification_id`, `collection_id`, `new_classification_id` |
| `POST` | `/zvideo/collection/split` | `dirSplit` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/share/record/list` | `settingShareList` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v2/new/status` | `getShareStatus` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/record/remove` | `rmShareDir` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/record/new` | `addShareDir` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/mode` | `getMode` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/mode/set` | `setMode` | `form-urlencoded` | `classification_mode` |
| `POST` | `/zvideo/classification/hide` | `setTvHide` | `form-urlencoded` | `classification_id`, `mobile`, `tv`, `web` |
| `POST` | `/zvideo/favorite/list` | `collectList` | `form-urlencoded` | `limit`, `start` |
| `POST` | `/zvideo/collection/filter` | `filter` | `form-urlencoded` | - |
| `POST` | `/zvideo/favorite/add` | `collect` | `form-urlencoded` | - |
| `POST` | `/zvideo/favorite/delete` | `uncollect` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/version/add` | `addVersion` | `form-urlencoded` | `classification_id`, `collection_id`, `path` |
| `POST` | `/zvideo/collection/version/remove` | `removeVersion` | `form-urlencoded` | `classification_id`, `collection_id`, `version_id` |
| `POST` | `/zvideo/collection/optimization` | `fixCollection` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/classification/dirs` | `getAllAddList` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/multihide` | `multihide` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v4/list` | `getShareClass` | `form-urlencoded` | `state` |
| `POST` | `/zvideo/share/v4/set` | `setShareClass` | `form-urlencoded` | `classification_id`, `share_users` |
| `POST` | `/zvideo/share/v4/collection/list` | `getShareClassList` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v4/collection/info` | `getShareClassDetail` | `form-urlencoded` | - |
| `POST` | `/zvideo/series/upload/cover` | `uploadCover` | `multipart/form-data` | - |
| `POST` | `/zvideo/series/download/cover` | `downloadCover` | `form-urlencoded` | `cover_url` |
| `POST` | `/zvideo/series/select/cover` | `selectSeriesCover` | `form-urlencoded` | `cover` |
| `POST` | `/zvideo/series/add` | `addCollection` | `form-urlencoded` | - |
| `POST` | `/zvideo/series/list` | `seriesList` | `form-urlencoded` | - |
| `POST` | `/zvideo/series/collection/list` | `seriesDetail` | `form-urlencoded` | - |
| `POST` | `/zvideo/series/delete` | `deleteSeries` | `form-urlencoded` | `classification_id`, `series_id` |
| `POST` | `/zvideo/series/collection/add` | `addToSeries` | `form-urlencoded` | `classification_id`, `collection_id`, `series_id` |
| `POST` | `/zvideo/series/edit` | `editSeries` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v4/series/collection/list` | `shareSeriesDetail` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v4/series/list` | `shareSeriesList` | `form-urlencoded` | - |
| `POST` | `/zvideo/series/collection/delete` | `removeFromSeries` | `form-urlencoded` | `classification_id`, `collection_id`, `series_id` |
| `POST` | `/zvideo/collection/add` | `addDiyCollection` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/edit` | `editDiyCollection` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/dismiss` | `delDiyCollection` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/collection/add/file` | `addToDiy` | `form-urlencoded` | `classification_id`, `collection_id`, `file_path` |
| `POST` | `/zvideo/collection/upload/cover` | `uploadDiyCover` | `multipart/form-data` | - |
| `POST` | `/zvideo/collection/download/cover` | `downloadDiyCover` | `form-urlencoded` | `cover_url` |
| `POST` | `/zvideo/collection/select/cover` | `selectDiyCover` | `form-urlencoded` | `cover` |
| `POST` | `/zvideo/collection/file/list` | `diyFileList` | `form-urlencoded` | `collection_id` |
| `POST` | `/zvideo/collection/delete/file` | `detailDiyFile` | `form-urlencoded` | `collection_id`, `file_path` |
| `POST` | `/zvideo/collection/alter` | `changeToDiy` | `form-urlencoded` | `classification_id`, `collection_id`, `custom_collection_id` |
| `POST` | `/zvideo/record/file/list` | `contentManageList` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/checkextdevstatus` | `checkExtDevStatus` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/rescan` | `classificationRescan` | `form-urlencoded` | - |
| `POST` | `/zvideo/classification/rescan/result` | `classificationRescanTask` | `form-urlencoded` | `task_id` |
| `POST` | `/zvideo/classification/customsort` | `changeClassSort` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/video/playlist/delete` | `memoryClean` | `form-urlencoded` | `playlist` |
| `POST` | `/zvideo/video/playlist/clear` | `memoryCleanAll` | `form-urlencoded` | - |
| `POST` | `/zvideo/video/person/list` | `personAlbumList` | `form-urlencoded` | - |
| `GET` | `/zvideo/services/status` | `servicesStatus` | `query` | - |
| `POST` | `/zvideo/task/quit` | `taskQuit` | `form-urlencoded` | `classification_id` |
| `POST` | `/zvideo/collection/report` | `collectionReport` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/collection/list` | `getShareCollectionList` | `form-urlencoded` | `num`, `start` |
| `POST` | `/zvideo/share/collection/delete` | `collectionDelete` | `form-urlencoded` | `ids` |
| `POST` | `/zvideo/share/collection/delete/by` | `collectionDeleteBy` | `form-urlencoded` | `state` |
| `POST` | `/zvideo/share/collection/modify` | `collectionModify` | `form-urlencoded` | `code`, `ids`, `state` |
| `POST` | `/zvideo/share/collection/modify/by` | `collectionModifyBy` | `form-urlencoded` | `newState`, `state` |
| `POST` | `/zvideo/record/notreadd/list` | `allDirInZvideoWithClassInfoList` | `form-urlencoded` | `classification_id`, `not_scrape` |
| `POST` | `/zvideo/collection/file/remove` | `collectionRemoveFile` | `form-urlencoded` | `classification_id`, `collection_id`, `file_path` |
| `POST` | `/zvideo/collection/episode` | `changeEp` | `form-urlencoded` | `collection_id` |
| `POST` | `/zvideo/collection/file/rematch` | `fileMatch` | `form-urlencoded` | `classification_id`, `collection_id`, `file_path`, `zrid` |
| `POST` | `/zvideo/search/celebrity` | `searchPeople` | `form-urlencoded` | `name` |
| `POST` | `/zvideo/collection/upload/backdrop` | `uploadBackdrop` | `multipart/form-data` | - |
| `POST` | `/zvideo/collection/download/backdrop` | `downloadBackdrop` | `form-urlencoded` | `backdrop_url` |
| `POST` | `/zvideo/collection/select/backdrop` | `selectDiyBackdrop` | `form-urlencoded` | `backdrop` |
| `POST` | `/zvideo/classification/series` | `autoSeriesSwitch` | `form-urlencoded` | `auto_series`, `classification_id` |
| `POST` | `/zvideo/classification/series/result` | `autoSeriesTask` | `form-urlencoded` | `task_id` |
| `POST` | `/zvideo/tmdb/search/seasons` | `getSeasons` | `form-urlencoded` | `zrid` |
| `POST` | `/zvideo/home/collection/suggested` | `recommendation` | `form-urlencoded` | `backdrop`, `limit` |
| `POST` | `/zvideo/collection/info` | `getInfoV2` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/collection/video` | `collectionVideoList` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/person/collection/list` | `personInfo` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/person` | `personList` | `form-urlencoded` | `collection_id` |
| `POST` | `/zvideo/share/classification/collection/info` | `getShareInfoV2` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/classification/collection/video` | `shareCollectionVideoList` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/home/share/collection/latest` | `homePageShareList` | `form-urlencoded` | `limit` |
| `POST` | `/zvideo/home/collection/latest` | `homePageAddList` | `form-urlencoded` | `limit` |
| `POST` | `/zvideo/settings/suggested/load` | `getRecommendSetting` | `form-urlencoded` | - |
| `POST` | `/zvideo/task/result` | `classificationSyncTask` | `form-urlencoded` | `task_id` |
| `POST` | `/zvideo/search/index` | `search` | `form-urlencoded` | `key_word` |
| `POST` | `/zvideo/search/suggest` | `searchSuggest` | `form-urlencoded` | `key_word` |
| `GET` | `/transcode/video/streams` | `getVideoStreamInfo` | `query` | `file_path` |
| `GET` | `/transcode/decrypt/video/streams` | `getVideoEncryptedStreamInfo` | `query` | `encrypted_path`, `from` |
| `POST` | `/zvideo/settings/sort/load` | `getDefaultSort` | `form-urlencoded` | `key` |
| `POST` | `/zvideo/settings/sort` | `setDefaultSort` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/enable` | `skipEnable` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/set` | `skipSet` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/classification` | `skipClassification` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/collection` | `skipCollection` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/task/info` | `skipTaskInfo` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/skip/task/result` | `skipTaskResult` | `form-urlencoded` | `classification_id`, `collection_id` |
| `POST` | `/zvideo/skip/task/action` | `skipTaskAction` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/task/edit` | `skipTaskEdit` | `form-urlencoded` | - |
| `POST` | `/zvideo/skip/task/list` | `skipTaskList` | `form-urlencoded` | - |
| `POST` | `/zvideo/share/v4/state` | `shareClassAcceptOrReject` | `form-urlencoded` | `classification_id`, `state` |
| `POST` | `/zvideo/classification/file/del` | `classificationFileDel` | `form-urlencoded` | `classification_id`, `file_path` |
| `POST` | `/zvideo/collection/file/unmatch` | `unMatchDir` | `form-urlencoded` | `classification_id`, `file_path` |
| `POST` | `/zvideo/task/cron/set` | `taskCronSet` | `form-urlencoded` | `enable`, `timer` |
| `POST` | `/zvideo/task/cron/info` | `taskCronInfo` | `form-urlencoded` | - |
| `POST` | `/zvideo/task/cron/result` | `taskCronResult` | `form-urlencoded` | - |
| `POST` | `/zvideo/task/cron/video/remove` | `taskCronVideoRemove` | `form-urlencoded` | - |
| `POST` | `/zvideo/task/cron/video/retain` | `taskCronVideoRetain` | `form-urlencoded` | - |
| `POST` | `/zvideo/collection/video/version` | `collectionVideoVersio` | `form-urlencoded` | - |
| `POST` | `/zvideo/emby/user/info` | `embyUserInfo` | `application/json` | - |
| `POST` | `/zvideo/emby/user/create` | `embyUserCreate` | `application/json` | `password`, `user_name` |
| `POST` | `/zvideo/emby/user/status` | `embyUserStatus` | `application/json` | `status` |
| `POST` | `/zvideo/rank/tmdb/collections` | `rankTmdbCollections` | `form-urlencoded` | `page`, `page_size`, `rank_type` |
| `POST` | `/zvideo/rank/tmdb/filters` | `rankTmdbFilters` | `form-urlencoded` | - |
| `POST` | `/zvideo/live` | `u` | `application/json` | - |
| `GET` | `/share/transcode/share/file/playaddr` | `getPlayUrl` | `query (direct)` | - |

### 4.4 Docker 容器、镜像、Compose 编排与 Portainer 引擎模块 (共 192 个端点)
涵盖 Webpack 模块 `94382` (极空间原生 `/zdocker/*` 接口：容器列表/状态/日志/Top/创建/更新/热更新/导出 Compose、本地镜像、常用镜像、Hub 镜像搜索与拉取、Docker Compose 项目编排、定时任务、镜像加速与私有仓库配置)、`33447` (Portainer 代理接口 `/docker/api/*` 与 `/settings?docker_status=true`)、`1552` (内置容器化应用控制 `/zdocker/app_*`)。

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/zdocker/storagepool_save_datadirconf` | `setDataDirConfig` | `application/json` | `mount` |
| `GET` | `/zdocker/storagepool_get_disksinfo` | `getStoragePoolList` | `query` | - |
| `GET` | `/zdocker/container_list` | `getContainers` | `query` | - |
| `GET` | `/zdocker/container_stats` | `getContainerStatus` | `query` | - |
| `GET` | `/zdocker/container_logs` | `getContainerLogs` | `query` | - |
| `GET` | `/zdocker/system_read_events_logs` | `getDockerEvents` | `query` | - |
| `GET` | `/zdocker/container_top` | `getContainerTop` | `query` | `container_id` |
| `GET` | `/zdocker/container_info` | `getContainerInfo` | `query` | `container_id` |
| `GET` | `/zdocker/container_op` | `containerOperation` | `query` | `action`, `container_id`, `id`, `name` |
| `POST` | `/zdocker/container_create?{{r}}` | `containerCreate` | `application/json` | `name` |
| `POST` | `/zdocker/container_update?{{r}}` | `containerUpdate` | `application/json` | `container_id`, `name` |
| `GET` | `/zdocker/local_image_list` | `getLocalImages` | `query` | - |
| `POST` | `/zdocker/v2/local_image_list` | `getLocalImagesV2` | `application/json` | - |
| `GET` | `/zdocker/common_image_list` | `getCommonImages` | `query` | - |
| `GET` | `/zdocker/common_image_search` | `searchCommonImages` | `query` | `image_name` |
| `GET` | `/zdocker/hub_image_search` | `searchHubImages` | `query` | `page`, `term` |
| `GET` | `/zdocker/hub_image_tags` | `getImageTags` | `query` | `is_official`, `repo_name` |
| `GET` | `/zdocker/image_info` | `getImageInfo` | `query` | `image_id` |
| `GET` | `/zdocker/common_image_download` | `downloadCommonImage` | `query` | `image_file_name` |
| `GET` | `/zdocker/common_image_download_task_cancel` | `cancelCommonTask` | `query` | `task_id` |
| `GET` | `/zdocker/image_pull` | `pullStorageImage` | `query` | `image_name` |
| `GET` | `/zdocker/image_pull_task_cancel` | `cancelPullImage` | `query` | `task_id` |
| `GET` | `/zdocker/image_pull_task_list` | `getPullTaskList` | `query` | - |
| `GET` | `/zdocker/common_image_download_task_list` | `getCommonTaskList` | `query` | - |
| `GET` | `/zdocker/image_import` | `importNasImage` | `query` | - |
| `GET` | `/zdocker/image_remove` | `deleteImage` | `query` | - |
| `GET` | `/zdocker/image_force_remove` | `forceDeleteImage` | `query` | - |
| `GET` | `/zdocker/network_list` | `getNetworkList` | `query` | - |
| `GET` | `/zdocker/network_info` | `getNetworkInfo` | `query` | `network_id` |
| `POST` | `/zdocker/network_create` | `createNetwork` | `application/json` | - |
| `GET` | `/zdocker/network_remove` | `deleteNetwork` | `query` | `network_id` |
| `GET` | `/zdocker/network_attach` | `attachNetwork` | `query` | `container_id`, `network_id` |
| `GET` | `/zdocker/network_detach` | `detachNetwork` | `query` | `container_id`, `network_id` |
| `GET` | `/zdocker/system_status` | `getDockerStatus` | `query` | - |
| `GET` | `/zdocker/system_switch` | `setDockerStatus` | `query` | `status` |
| `POST` | `/zdocker/system_restart` | `restartDocker` | `form-urlencoded` | - |
| `POST` | `/zdocker/system_reset` | `resetDocker` | `form-urlencoded` | - |
| `GET` | `/zdocker/system_get_proxy_conf` | `getProxyConfig` | `query` | - |
| `POST` | `/zdocker/system_save_proxy_conf` | `setProxyConfig` | `application/json` | - |
| `GET` | `/zdocker/v2/registry_mirrors` | `getRegistry` | `query` | - |
| `POST` | `/zdocker/v2/registry_mirrors` | `setRegistry` | `application/json` | - |
| `GET` | `/zdocker/system_get_private_hub` | `getPrivateHub` | `query` | - |
| `POST` | `/zdocker/system_save_private_hub` | `setPrivateHub` | `application/json` | - |
| `GET` | `/zdocker/storagepool_get_datadirconf` | `getDataDirConfig` | `query` | - |
| `GET` | `/zdocker/storagepool_get_datadirusage` | `getDataDirUsage` | `query` | - |
| `GET` | `/zdocker/storagepool_get_datadircopy_tasks` | `getDataDirCopyTask` | `query` | - |
| `GET` | `/zdocker/system_info` | `getServeStatus` | `query` | - |
| `GET` | `/zdocker/system_check_upgrade` | `checkUpdate` | `query` | - |
| `GET` | `/zdocker/system_upgrade` | `dockerUpdate` | `query` | - |
| `GET` | `/zdocker/system_upgrade_task_list` | `getUpgradeTaskList` | `query` | - |
| `POST` | `/zdocker/v2/compose/list_projects` | `getComposeListV2` | `application/json` | - |
| `GET` | `/zdocker/v2/compose/list_project_containers` | `getComposeContainerList` | `query` | - |
| `GET` | `/zdocker/v2/compose/logs_project` | `getComposeLogs` | `query` | - |
| `GET` | `/zdocker/v2/compose/get_project_file` | `getComposeFile` | `query` | `project_id` |
| `GET` | `/zdocker/v2/compose/check_projectname` | `checkComposePrName` | `query` | `project_name` |
| `GET` | `/zdocker/compose_start_project` | `startDockerCompose` | `query` | - |
| `GET` | `/zdocker/compose_stop_project` | `stopDockerCompose` | `query` | - |
| `GET` | `/zdocker/v2/compose/delete_project` | `deleteDockerCompose` | `query` | - |
| `GET` | `/zdocker/compose_restart_project` | `restartDockerCompose` | `query` | - |
| `GET` | `/zdocker/v2/compose/rebuild_project` | `rebuildDockerCompose` | `query` | `project_id`, `pull`, `start` |
| `GET` | `/zdocker/v2/compose/task_list` | `getComposeTaskList` | `query` | - |
| `GET` | `/zdocker/v2/compose/task_status` | `getComposeTaskStatus` | `query` | - |
| `GET` | `/zdocker/v2/compose/task_cancel` | `cancelComposeTask` | `query` | `task_id` |
| `GET` | `/zdocker/network_get_ip6tables_enable` | `getIpv6firewallStatus` | `query` | - |
| `GET` | `/zdocker/network_set_ip6tables_enable` | `setIpv6firewallStatus` | `query` | `ip6tables` |
| `POST` | `/zdocker/container_hotupdate?{{r}}` | `containerHotUpdate` | `application/json` | `container_id`, `name` |
| `POST` | `/zdocker/container_convert_compose` | `containerExport` | `application/json` | `container_id`, `dir` |
| `GET` | `/zdocker/storagepool_update_disksinfo` | `refreshPool` | `query` | - |
| `POST` | `/zdocker/image_update` | `image_update` | `application/json` | `containers`, `repotag` |
| `POST` | `/zdocker/container_stats_style` | `setCpuUsageStyle` | `application/json` | `cpu_usage` |
| `GET` | `/zdocker/container_stats_style` | `getCpuUsageStyle` | `query` | - |
| `POST` | `/zdocker/v2/compose/create_project` | `createDockerComposV2` | `form-urlencoded` | - |
| `GET` | `/zdocker/v2/compose/check_projectpath` | `checkPathHasYmlAndEnv` | `query` | `project_path` |
| `POST` | `/zdocker/v2/compose/cron` | `setComposeCron` | `form-urlencoded` | `cron_config`, `project_name` |
| `GET` | `/zdocker/v2/compose/cron` | `getComposeCron` | `query` | `project_name` |
| `GET` | `/zdocker/docker_info` | `getDockerVersionInfo` | `query` | - |
| `POST` | `/zdocker/v2/container_list_filter_order` | `setOrderType` | `application/json` | `order` |
| `GET` | `/zdocker/v2/container_list_filter_order` | `getOrderType` | `query` | - |
| `POST` | `/zdocker/v2/container_list_filter_sort` | `setSortType` | `application/json` | `sort_by` |
| `GET` | `/zdocker/v2/container_list_filter_sort` | `getSortType` | `query` | - |
| `POST` | `/zdocker/v2/compose/project_list_filter_order` | `setComposeOrderType` | `application/json` | `order` |
| `GET` | `/zdocker/v2/compose/project_list_filter_order` | `getComposeOrderType` | `query` | - |
| `POST` | `/zdocker/v2/compose/project_list_filter_sort` | `setComposeSortType` | `application/json` | `sort_by` |
| `GET` | `/zdocker/v2/compose/project_list_filter_sort` | `getComposeSortType` | `query` | - |
| `POST` | `/zdocker/v2/compose/project_list_filter_state` | `setComposeFilterStatus` | `application/json` | `state` |
| `GET` | `/zdocker/v2/compose/project_list_filter_state` | `getComposeFilterStatus` | `query` | - |
| `POST` | `/zdocker/v2/image_list_filter_order` | `setImageOrderType` | `application/json` | `order` |
| `GET` | `/zdocker/v2/image_list_filter_order` | `getImageOrderType` | `query` | - |
| `POST` | `/zdocker/v2/image_list_filter_sort` | `setImageSortType` | `application/json` | `sort_by` |
| `GET` | `/zdocker/v2/image_list_filter_sort` | `getImageSortType` | `query` | - |
| `POST` | `/zdocker/v2/container_list_filter_class` | `setFilterType` | `application/json` | - |
| `GET` | `/zdocker/v2/container_list_filter_class` | `getFilterType` | `query` | - |
| `POST` | `/zdocker/v2/container_list_filter_state` | `setFilterStatus` | `application/json` | `state` |
| `GET` | `/zdocker/v2/container_list_filter_state` | `getFilterStatus` | `query` | - |
| `POST` | `/zdocker/v2/container_list` | `getContainersV2` | `application/json` | - |
| `GET` | `/docker/api/status` | `getPortainerStatus` | `query` | - |
| `GET` | `/settings?docker_status=true` | `dockerStatus` | `query` | - |
| `GET` | `/settings` | `dockerDo` | `query` | - |
| `GET` | `/docker/api/users/admin/check` | `checkAdmin` | `query` | - |
| `POST` | `/docker/api/users/admin/init` | `initAdmin` | `multipart/form-data` | - |
| `POST` | `/docker/api/auth` | `auth` | `application/json` | `nid`, `t`, `uuid` |
| `POST` | `/docker/api/endpoints` | `connectEndpoint` | `form-urlencoded` | - |
| `GET` | `/docker/api/endpoints` | `getEndpoints` | `query` | - |
| `GET` | `/docker/api/endpoints/{{o(}}` | `getContainers` | `query` | - |
| `GET` | `/settings?container_info=true` | `getContainerMem` | `query` | - |
| `REMOVE` | `/docker/api/endpoints/{{o(}}` | `containerOperation` | `multipart/form-data` | `action`, `container_id`, `id`, `name` |
| `POST` | `/settings?create_container=true&endPointId={{o(}}` | `createContainer` | `application/json` | - |
| `POST` | `/docker/api/endpoints/{{o(}}` | `renameContainer` | `application/json` | - |
| `GET` | `/settings?docker_stop_container=true&cid={{t}}` | `forceDelContainer` | `query` | - |
| `GET` | `/settings?search_image=true` | `searchImage` | `query` | - |
| `GET` | `/settings?pull_image=true` | `createImageNas` | `query` | - |
| `GET` | `/settings?pull_image_ex=true` | `createImageNasAsync` | `query` | - |
| `GET` | `/settings?add_image=true` | `addImageInfo` | `query` | - |
| `GET` | `/settings?del_image=true` | `delImageInfo` | `query` | - |
| `GET` | `/settings?get_image=true` | `getImageInfo` | `query` | `image_id` |
| `GET` | `/settings?tags_image=true` | `getImageVersionInfo` | `query` | - |
| `DELETE` | `/docker/api/endpoints/{{o(}}` | `delImage` | `query` | - |
| `POST` | `/settings?load_image=true` | `loadNasImage` | `form-urlencoded` | - |
| `POST` | `/settings?load_image_ex=true` | `loadNasImageAsync` | `form-urlencoded` | - |
| `GET` | `/settings?check_ifname=true` | `checkIfNames` | `query` | - |
| `GET` | `/settings?set_ifname=true` | `setIfName` | `query` | - |
| `GET` | `/settings?add_network=true` | `addVirtualNetwork` | `query` | - |
| `GET` | `/settings?del_network=true` | `delVirtualNetwork` | `query` | - |
| `GET` | `/settings?get_network_list=true` | `getVirtualNetworks` | `query` | - |
| `GET` | `/settings?inbond=true` | `setBond` | `query` | - |
| `GET` | `/settings?unbond=true` | `unBond` | `query` | - |
| `POST` | `/settings?net_unbond_config=true` | `setUnbondIp` | `form-urlencoded` | - |
| `GET` | `/settings?get_hub=true` | `getRegistrys` | `query` | - |
| `GET` | `/settings?add_hub=true` | `addRegistry` | `query` | - |
| `GET` | `/settings?mod_hub=true` | `modifyRegistry` | `query` | - |
| `GET` | `/docker/api/registries` | `getPortainerRegistry` | `query` | - |
| `POST` | `/docker/api/registries` | `setPortainerRegistry` | `multipart/form-data` | - |
| `GET` | `/settings?del_hub=true` | `delRegistry` | `query` | - |
| `GET` | `/settings?set_hub=true` | `usedRegistry` | `query` | - |
| `GET` | `/settings?clean_logs=true` | `cleanDockerLogs` | `query` | `cid` |
| `GET` | `/settings?default_image=true` | `zspaceDefaultImages` | `query` | - |
| `GET` | `/settings?docker_clean` | `cleanPortainer` | `query` | - |
| `GET` | `/settings?container_stop_all` | `stopAllContainer` | `query` | - |
| `GET` | `/settings?docker_reload=true` | `reloadDockerEngine` | `query` | - |
| `GET` | `/settings?docker_container_start=true` | `changeContainerPermission` | `query` | - |
| `GET` | `/settings?stop_pull_image=true` | `stopPullImage` | `query` | - |
| `GET` | `/settings?download_status=true` | `getTaskStatus` | `query` | - |
| `GET` | `/settings?docker_check_storage=true` | `checkDockerStorage` | `query` | - |
| `GET` | `/settings?docker_del_data=true` | `deleteDockerData` | `query` | - |
| `GET` | `/settings?get_pool_info=true` | `getPoolInfo` | `query` | - |
| `POST` | `/settings?set_storage_info=true` | `setDockerStorageInfo` | `form-urlencoded` | - |
| `GET` | `/settings?get_storage_info=true` | `getDockerStorageInfo` | `query` | - |
| `GET` | `/settings?docker_remove=true` | `startDockerRemove` | `query` | - |
| `GET` | `/settings?get_remove_status=true` | `getRemoveStatus` | `query` | - |
| `GET` | `/settings?docker_mount_point=true` | `getDockerMountPoint` | `query` | - |
| `GET` | `/settings?get_root_size=true` | `getDockerEnginerSize` | `query` | - |
| `GET` | `/settings?docker_container_list=true` | `getDockerContainerSize` | `query` | - |
| `GET` | `/settings?del_container=true` | `delDockerContainerSize` | `query` | - |
| `GET` | `/settings?mkdir=true` | `createMountRealPath` | `query` | - |
| `GET` | `/settings?bind_file=true` | `getFileRealPathMount` | `query` | - |
| `GET` | `/settings?check_user_root=true` | `checkUserRoot` | `query` | `mount` |
| `GET` | `/settings?enginer_error=true` | `checkEnginerError` | `query` | - |
| `POST` | `/settings?check_ports=true` | `checkContainerPorts` | `form-urlencoded` | - |
| `GET` | `/settings?disk_remove_check=true` | `diskRemoveCheck` | `query` | `mount` |
| `GET` | `/settings?is_docker_in_ssd=true` | `checkIsDockerInM2` | `query` | - |
| `GET` | `/settings?get_cores=true` | `getCores` | `query` | - |
| `GET` | `/settings?check_update=true` | `checkUpdate` | `query` | - |
| `GET` | `/settings?docker_update=true` | `dockerUpdate` | `query` | - |
| `GET` | `/settings?docker_update_status=true` | `dockerUpdateStatus` | `query` | - |
| `GET` | `/settings?docker_get_proxy=true` | `dockerGetProxy` | `query` | - |
| `POST` | `/settings?docker_set_proxy=true` | `dockerSetProxy` | `form-urlencoded` | - |
| `POST` | `/settings?download_image=true` | `downloadFreqImage` | `form-urlencoded` | - |
| `GET` | `/settings?download_process=true` | `downloadFreqImageStatus` | `query` | - |
| `GET` | `/settings?stop_download=true` | `stopFreqDownload` | `query` | - |
| `GET` | `/settings?local_search=true` | `localImageSearch` | `query` | - |
| `POST` | `/setting/sshd/status` | `getSSHStatus` | `form-urlencoded` | - |
| `GET` | `/settings?android_check=true` | `androidCheck` | `query` | - |
| `POST` | `/settings?android_create_container=true` | `createAndroidContainer` | `form-urlencoded` | `auto`, `desc`, `name`, `path` |
| `GET` | `/settings?android_get_config=true` | `getAndroidConfig` | `query` | `name` |
| `GET` | `/settings?android_del_config=true` | `delAndroidConfig` | `query` | `force`, `name` |
| `GET` | `/settings?android_container_event=true` | `androidContainerEvent` | `query` | `event`, `name` |
| `GET` | `/settings?android_rename=true` | `androidContainerRename` | `query` | `desc`, `name` |
| `GET` | `/settings?load_process=true` | `androidLoadingProgress` | `query` | - |
| `GET` | `/settings?image_check=true` | `androidImageCheck` | `query` | `name` |
| `GET` | `/settings?image_update=true` | `androidImageUpdate` | `query` | `name`, `tag` |
| `GET` | `/settings?android_get_hdmi_config=true` | `androidHdmiConfigGet` | `query` | - |
| `GET` | `/settings?android_create_hdmi=true` | `androidHdmiCreate` | `query` | `name`, `port` |
| `GET` | `/settings?android_delete_hdmi=true` | `androidHdmiDelete` | `query` | `name`, `port` |
| `GET` | `/zdocker/app_info` | `appInfo` | `query` | - |
| `GET` | `/zdocker/app_remove` | `appRemove` | `query` | - |
| `GET` | `/zdocker/app_check_port` | `appCheckPort` | `query` | `port` |
| `GET` | `/zdocker/app_state` | `appState` | `query` | - |
| `GET` | `/zdocker/app_start` | `appStart` | `query` | - |
| `GET` | `/zdocker/app_stop` | `appStop` | `query` | - |
| `GET` | `/zdocker/app_open` | `appOpen` | `query` | `name` |
| `GET` | `/zdocker/app_download` | `appDownload` | `query` | `image_file_name` |
| `POST` | `/zdocker/app_install` | `appInstall` | `application/json` | - |

### 4.5 下载工具模块：下载中心 (PT/BT/HTTP)、种子解析、任务队列与 FTP/Rsync 下载 (共 65 个端点)
涵盖 Webpack 模块 `14530` (核心下载中心 `/downloader/*`：添加磁力/HTTP链接、上传/解析 NAS 内 BT 种子、Tracker/Peer/文件选择、做种控制、下载目录与引擎切换)、`63775` (分享下载队列 `/downloader/share/*`、后台文件任务 `/task/*`、解压任务 `/uncompress/*`)、`67288` (FTP/Rsync 远程服务器连接与下载 `/ftprsync/*`)。
此外，迅雷组件通过 `GET /thunder/getWebUrl` 获取嵌入式 Web 控制面板地址；视频链接下载器使用 `/converter/v1/download/*`。

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/downloader/add/link` | `n` | `form-urlencoded` | - |
| `POST` | `/downloader/list` | `getTask` | `form-urlencoded` | `size`, `start` |
| `POST` | `/downloader/get/protocols` | `a` | `form-urlencoded` | - |
| `POST` | `/downloader/del` | `o` | `form-urlencoded` | - |
| `POST` | `/downloader/add/torrent` | `uploadBT` | `multipart/form-data` | - |
| `POST` | `/downloader/start` | `startTask` | `form-urlencoded` | `id` |
| `POST` | `/downloader/stop` | `stopTask` | `form-urlencoded` | - |
| `POST` | `/downloader/add/torrent/nas` | `h` | `form-urlencoded` | - |
| `POST` | `/downloader/del/all` | `delAll` | `form-urlencoded` | - |
| `POST` | `/downloader/start/all` | `startAll` | `form-urlencoded` | - |
| `POST` | `/downloader/stop/all` | `stopAll` | `form-urlencoded` | - |
| `POST` | `/downloader/get/setting` | `getSetting` | `form-urlencoded` | - |
| `POST` | `/downloader/tasks/details` | `downLoadDetails` | `form-urlencoded` | - |
| `POST` | `/downloader/start/seeding/all` | `v` | `form-urlencoded` | - |
| `POST` | `/downloader/stop/seeding/all` | `g` | `form-urlencoded` | - |
| `POST` | `/downloader/storage/move` | `storageMove` | `form-urlencoded` | `pname` |
| `POST` | `/downloader/set/setting` | `downLoadSetting` | `form-urlencoded` | `download_queue_size` |
| `POST` | `/downloader/preprocess/torrent/parse/upload` | `preProcessBT` | `multipart/form-data` | - |
| `POST` | `/downloader/preprocess/torrent/parse/nas` | `preProcessNasBT` | `form-urlencoded` | `path` |
| `POST` | `/downloader/preprocess/torrent/effect` | `preProcessEffect` | `form-urlencoded` | - |
| `POST` | `/downloader/preprocess/torrent/cancel` | `preProcessCancel` | `form-urlencoded` | `hash` |
| `POST` | `/downloader/top/set` | `topSet` | `form-urlencoded` | `id` |
| `POST` | `/downloader/top/cancel` | `topCancel` | `form-urlencoded` | `id` |
| `POST` | `/downloader/verify` | `verifyTask` | `form-urlencoded` | `id` |
| `POST` | `/downloader/torrent/get/generic` | `torrentGetGeneric` | `form-urlencoded` | `id` |
| `POST` | `/downloader/torrent/set/generic` | `torrentSetGeneric` | `form-urlencoded` | - |
| `POST` | `/downloader/downloaddir` | `downloaddir` | `form-urlencoded` | - |
| `POST` | `/downloader/torrent/view/tracker` | `torrentViewTracker` | `form-urlencoded` | - |
| `POST` | `/downloader/torrent/add/tracker` | `addTracker` | `form-urlencoded` | `id`, `tracker` |
| `POST` | `/downloader/torrent/del/tracker` | `delTracker` | `form-urlencoded` | `id`, `trackerId` |
| `POST` | `/downloader/torrent/view/peer` | `torrentViewPeer` | `form-urlencoded` | - |
| `POST` | `/downloader/torrent/view/file` | `torrentViewFile` | `form-urlencoded` | - |
| `POST` | `/downloader/torrent/set/file` | `torrentSetFile` | `form-urlencoded` | `fileId`, `id`, `option` |
| `POST` | `/downloader/storage/confirm` | `storageConfirm` | `form-urlencoded` | - |
| `POST` | `/downloader/engin/switch/exec` | `enginSwitch` | `form-urlencoded` | - |
| `POST` | `/downloader/engin/switch/status` | `enginSwitchStatus` | `form-urlencoded` | - |
| `POST` | `/downloader/engin/switch/confirm` | `enginSwitchConfirm` | `form-urlencoded` | - |
| `POST` | `/downloader/share/list` | `getShareList` | `form-urlencoded` | `num`, `size`, `start`, `type` |
| `POST` | `/downloader/share/start` | `startShareOne` | `form-urlencoded` | `id` |
| `POST` | `/downloader/share/retry/all` | `retryShareAll` | `form-urlencoded` | - |
| `POST` | `/downloader/share/stop/all` | `stopShareAll` | `form-urlencoded` | - |
| `POST` | `/downloader/share/start/all` | `startShareAll` | `form-urlencoded` | - |
| `POST` | `/task/list` | `fileTaskList` | `form-urlencoded` | - |
| `POST` | `/task/redo` | `fileTaskRedo` | `form-urlencoded` | - |
| `POST` | `/downloader/share/stop` | `stopShareOne` | `form-urlencoded` | `id` |
| `POST` | `/downloader/share/del/complete` | `delComplete` | `form-urlencoded` | - |
| `POST` | `/downloader/share/del/all` | `delAll` | `form-urlencoded` | - |
| `POST` | `/downloader/share/del/fail` | `delFail` | `form-urlencoded` | - |
| `POST` | `/downloader/share/del/task` | `delOne` | `form-urlencoded` | `id` |
| `POST` | `/task/redoall` | `fileTaskRedoAll` | `form-urlencoded` | - |
| `POST` | `/uncompress/list` | `uncompressList` | `form-urlencoded` | - |
| `POST` | `/uncompress/delete` | `uncompressDelete` | `form-urlencoded` | - |
| `POST` | `/uncompress/clean` | `uncompressClean` | `form-urlencoded` | - |
| `POST` | `/uncompress/redo` | `uncompressRedo` | `form-urlencoded` | - |
| `GET` | `/ftprsync/checklogin` | `checkLogin` | `query` | - |
| `POST` | `/ftprsync/connect` | `connect` | `form-urlencoded` | `srv_addr` |
| `POST` | `/ftprsync/login` | `login` | `form-urlencoded` | `auth_code`, `passwd`, `srv_addr`, `user` |
| `POST` | `/ftprsync/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `GET` | `/ftprsync/dir/list` | `list` | `query` | `path`, `srv_addr` |
| `POST` | `/ftprsync/download/add` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `GET` | `/ftprsync/download/list` | `taskList` | `query` | `size`, `start`, `state`, `type` |
| `POST` | `/ftprsync/download/del` | `delTask` | `form-urlencoded` | - |
| `POST` | `/ftprsync/download/resume` | `startTask` | `form-urlencoded` | `id` |
| `POST` | `/ftprsync/download/pause` | `stopTask` | `form-urlencoded` | - |
| `POST` | `/ftprsync/download/retry` | `retryTask` | `form-urlencoded` | - |

### 4.6 极相册 (ZAlbum)、AI 人脸/场景识别、RAG 自然语言搜图与超分修复模块 (共 197 个端点)
涵盖 Webpack 模块 `74922` (极相册核心 `/v2/album/*`：时间线、相册分类、家庭/宝宝相册、AI 人脸聚类/宠物/场景/OCR/相似图清理/那年今日、地图足迹、外链分享、相册数据库迁移与备份)、`8201` (相册 AI RAG 自然语言搜图与索引 `/v2/album/rag/*`)、`14404` (照片 AI 超分辨率修复 `/v2/album/feed/sr/*`)。

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/v2/album/feed/timeline` | `getTimeline` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/feed/sections` | `getPhotosBySection` | `form-urlencoded` | `album_id`, `day`, `liked`, `num`, `order`, `start` |
| `POST` | `/v2/album/conf/save` | `setAIConfig` | `form-urlencoded` | `ai_main_open`, `ai_ocr_open`, `ai_pet_open`, `ai_recommend_open`, `ai_search_open`, `ai_similarity`, `history_today_open`, `k`, `laplacian_threshold`, `open`, `rag_open`, `scene_open` |
| `POST` | `/transcode/thumb/exif` | `changeExif` | `form-urlencoded` | `file_path`, `latitude`, `longitude`, `orientation` |
| `POST` | `/v2/album/album/albums/by_feed_id` | `albumForPic` | `form-urlencoded` | `feed_id` |
| `POST` | `/v2/album/ai/history/today` | `g` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/history/today/persons/not_display` | `notDisplayPerson` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/musicList` | `f` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/taskExportCreate` | `_` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/taskCheck` | `v` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/taskRetry` | `b` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/taskConform` | `y` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/taskManager` | `getAiTask` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/taskEventList` | `getAiTaskList` | `form-urlencoded` | - |
| `POST` | `/v2/album/albumsub/subConfSave` | `subConfSave` | `form-urlencoded` | - |
| `POST` | `/v2/album/albumsub/subConfInfo` | `getSubConfInfo` | `form-urlencoded` | `subType` |
| `POST` | `/v2/album/albumsub/getRefreshStatus` | `getRefreshStatus` | `form-urlencoded` | `playlist_id`, `subConfId`, `updateType` |
| `POST` | `/v2/album/albumsub/getSubAlbumList` | `getSubAlbumList` | `form-urlencoded` | `subConfId`, `updateType` |
| `POST` | `/v2/album/albumsub/refresh` | `refreshSubconfig` | `form-urlencoded` | `subConfId`, `updateType` |
| `POST` | `/v2/album/albumsub/subConfClean` | `subconfclean` | `form-urlencoded` | `subConfId`, `updateType` |
| `POST` | `/v2/album/db/checkStatus` | `albumDbCheckStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/size` | `albumDbSize` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/update` | `albumDbUpdate` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/updateProgress` | `albumDbUpdateProgress` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/taskMigrateCreate` | `albumDbTaskMigrateCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/taskMigrateProgress` | `albumDbMigrateProgress` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/backupInfo` | `albumDbBackupInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/db/backupUpdate` | `albumDbBackupUpdate` | `form-urlencoded` | `frequency_days`, `retain_num` |
| `POST` | `/v2/album/feed/tidy/list` | `getFeedTidyList` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/tidy/random` | `getFeedTidyRandom` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/tidy/done` | `setFeedIdsTidyDone` | `form-urlencoded` | `feedIds` |
| `POST` | `/v2/album/feed/tidy/date` | `setDateFeedTidyStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/tidy/dateList` | `getTidyedDateList` | `form-urlencoded` | - |
| `POST` | `/album/photos` | `getAllPhotos` | `form-urlencoded` | - |
| `POST` | `/album/videos` | `getAllVideos` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/list` | `getPhotoVideos` | `form-urlencoded` | `num` |
| `POST` | `/v2/album/album/feeds` | `getAlbumFiles` | `form-urlencoded` | - |
| `POST` | `/v2/album/tag/all` | `getAlbumAllTags` | `form-urlencoded` | - |
| `POST` | `/v2/album/tag/photos` | `getAlbumTagPhotos` | `form-urlencoded` | - |
| `POST` | `/v2/album/albums` | `getAlbumList` | `form-urlencoded` | `isAutoHideAiFace`, `types` |
| `POST` | `/v2/album/home` | `getAlbumHome` | `form-urlencoded` | - |
| `POST` | `/v2/album/ilikelist` | `getILikeList` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/like` | `like` | `form-urlencoded` | - |
| `POST` | `/v2/album/modify` | `modify` | `form-urlencoded` | `dwstate`, `newname`, `path` |
| `POST` | `/v2/album/share/switch` | `modify2` | `form-urlencoded` | `album_id`, `is_share` |
| `POST` | `/v2/album/album/delete` | `removeAlbum` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/feed/delete` | `removefile` | `form-urlencoded` | - |
| `POST` | `/v2/album/nasshare` | `nasshare` | `form-urlencoded` | - |
| `POST` | `/v2/album/dirs` | `getAlbumSys` | `form-urlencoded` | `has_cover` |
| `POST` | `/v2/file/list` | `getFileList` | `form-urlencoded` | `driveId`, `folderId`, `ftype`, `loadMore`, `num`, `order`, `path`, `sortby`, `start` |
| `POST` | `/v2/album/album/create` | `createAlbum` | `form-urlencoded` | - |
| `GET` | `t.encrypted_path?"/transcode/decrypt/playaddr":"/transcode/playaddr"` | `getVideoPlayUrl` | `query` | `audio_codec`, `dest_fmt`, `encrypted_path`, `file_path`, `from`, `video_codec` |
| `GET` | `t.encrypted_path?"/transcode/decrypt/play":"/transcode/play"` | `getVideoPlayUrlV2` | `query` | `audio_codec`, `audio_index`, `dest_fmt`, `encrypted_path`, `file_path`, `from`, `play_env`, `subtitle_index`, `video_codec` |
| `GET` | `t.encrypted_path?"/transcode/decrypt/subtitle":"/transcode/subtitle"` | `getVideoSubtitle` | `query` | `encrypted_path`, `file_path`, `from`, `specify_subtitles` |
| `GET` | `t.encrypted_path?"/transcode/decrypt/formatmenu":"/transcode/formatmenu"` | `getVideoFormatMenu` | `query` | - |
| `GET` | `t.encrypted_path?"/transcode/decrypt/format":"/transcode/format"` | `getVideoFormat` | `query` | `audio_codec`, `dest_fmt`, `encrypted_path`, `file_path`, `play_env`, `video_codec` |
| `POST` | `(null==t?void 0:t.isDecrypt)?"/transcode/decrypt/play/stop":"/transcode/play/stop"` | `stopVideo` | `form-urlencoded` | `isDecrypt`, `play_addr` |
| `POST` | `/play/switchtrack` | `switchtrack` | `form-urlencoded` | `audioIndex`, `rateIndex`, `subtitleIndex` |
| `POST` | `/v2/album/setfeeddir` | `setFeedDir` | `form-urlencoded` | - |
| `POST` | `/setting/load` | `shareAlbumCode` | `form-urlencoded` | - |
| `POST` | `/v2/album/share` | `shareAlbumCode` | `form-urlencoded` | - |
| `POST` | `/v2/album/share/file` | `shareAlbumCodeV2` | `form-urlencoded` | - |
| `POST` | `/v2/album/share/create` | `newShareAlbumCode` | `form-urlencoded` | `album_id`, `expire`, `liked` |
| `POST` | `/v2/album/scan/new` | `scanNew` | `form-urlencoded` | - |
| `POST` | `/v2/album/scan/info` | `scanInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/flush/info` | `flushInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/dirs/add` | `setAlbumDir` | `form-urlencoded` | `paths` |
| `POST` | `/v2/album/dirs/add/no_flush` | `setAlbumDir_noflush` | `form-urlencoded` | `paths` |
| `POST` | `/v2/album/flush/all` | `flushAll` | `form-urlencoded` | - |
| `POST` | `/v2/album/dirs/flush` | `flushDir` | `form-urlencoded` | `dir_ids` |
| `POST` | `/v2/album/flush/clean` | `flushClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/dirs/delete` | `removeDir` | `form-urlencoded` | `dir_ids` |
| `POST` | `/v2/album/position` | `getPositionTag` | `form-urlencoded` | `lat`, `lng` |
| `POST` | `/v2/album/positionList` | `getPositionTags` | `form-urlencoded` | `lat`, `lng` |
| `POST` | `/v2/album/album/unbind` | `albumUnbind` | `form-urlencoded` | `album_id`, `src_album_id` |
| `POST` | `/v2/album/album/change` | `albumAttrChange` | `form-urlencoded` | `album_id`, `bbc_id`, `bbc_position`, `can_shared`, `cover_id`, `delete_ids`, `disable_ids`, `feed_hide`, `hide`, `iam`, `role` |
| `POST` | `/v2/album/album/feed/kick` | `kickAIPhoto` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/merge2` | `mergeFaceAlbum` | `form-urlencoded` | `album_ids`, `to_album_id` |
| `POST` | `/v2/album/album/feed/move` | `moveToFaceAlbum` | `form-urlencoded` | `album_id`, `feed_ids`, `to_album_id` |
| `POST` | `/v2/album/conf/load` | `getAIConfig` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/run` | `runAI` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/clean` | `aiClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/recluster` | `reclusterAI` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/info` | `getAlbumInfo` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/modify` | `modifyAlbumInfo` | `form-urlencoded` | `album_id`, `desc`, `name`, `perm`, `users` |
| `POST` | `/v2/album/album/quit` | `quitPubAlbum` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/feed/add` | `albumMultiAdd` | `form-urlencoded` | `album_id`, `feed_ids`, `monit`, `paths` |
| `POST` | `/v2/album/ai/state` | `getAIState` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/binds` | `getAlbumBind` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/flush` | `albumFlush` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/moments` | `babyAlbumMoments` | `form-urlencoded` | `album_id` |
| `POST` | `/v2/album/album/visible` | `batchChangeVisible` | `form-urlencoded` | `album_ids`, `hide` |
| `POST` | `/v2/album/album/sequence` | `sortAlbum` | `form-urlencoded` | `seq` |
| `POST` | `/v2/album/album/ai/role` | `relationWithMe` | `form-urlencoded` | `album_ids`, `role` |
| `POST` | `/v2/album/feed/geo_hash_group` | `geoHashGroup` | `form-urlencoded` | - |
| `POST` | `/v2/album/share/list` | `albumShareList` | `form-urlencoded` | `num`, `start` |
| `POST` | `/v2/album/share/modify` | `albumShareModify` | `form-urlencoded` | `code`, `ids`, `state` |
| `POST` | `/v2/album/share/delete` | `albumShareDelete` | `form-urlencoded` | `ids` |
| `POST` | `/v2/album/share/modify/by` | `albumShareModifyBy` | `form-urlencoded` | `newState`, `state` |
| `POST` | `/v2/album/share/delete/by` | `albumShareDeleteBy` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/search` | `albumFeedSearch` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/ocr/create` | `albumFeedOcrCreate` | `form-urlencoded` | `image_path` |
| `POST` | `/v2/album/feed/ocr/query` | `albumFeedOcrQuery` | `form-urlencoded` | `image_path` |
| `POST` | `/v2/album/feed/search/create` | `albumFeedSearchCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/search/query` | `albumFeedSearchQuery` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/ocr/search` | `searchOCR` | `form-urlencoded` | - |
| `POST` | `/v2/album/search/create` | `albumSearchCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/search/query` | `albumSearchQuery` | `form-urlencoded` | - |
| `POST` | `/v2/album/model/download/check` | `albumModelDownloadCheck` | `form-urlencoded` | `engineType` |
| `POST` | `/v2/album/model/download/info` | `albumModelDownloadInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/model/download/zspool` | `albumModelDownloadZspool` | `form-urlencoded` | `engineType` |
| `POST` | `/v2/album/model/download/create` | `albumModelDownloadCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/model/download/suspend` | `albumModelDownloadSuspend` | `form-urlencoded` | `ID` |
| `POST` | `/v2/album/model/download/resume` | `albumModelDownloadResume` | `form-urlencoded` | `ID` |
| `POST` | `/v2/album/feed/search/ai_state` | `albumFeedSearchAi_state` | `form-urlencoded` | - |
| `POST` | `/v2/album/model/download/clean` | `albumModelDownloadClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/role/list` | `albumRoleList` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/role/add` | `albumRoleAdd` | `form-urlencoded` | `role` |
| `POST` | `/v2/album/album/role/delete` | `albumRoleDelete` | `form-urlencoded` | `role_id` |
| `POST` | `/v2/album/album/posting/home` | `albumPostingHome` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/posting/list` | `albumPostingList` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/posting/create` | `albumPostingCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/comments/create` | `albumCommentsCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/comments/delete` | `albumCommentsDelete` | `form-urlencoded` | `album_id`, `cid`, `gidx`, `id` |
| `POST` | `/v2/album/album/comments/list` | `albumCommentsList` | `form-urlencoded` | `album_id`, `theme_id` |
| `POST` | `/v2/album/album/posting/like` | `albumPostingListLiked` | `form-urlencoded` | `autograph_id`, `id`, `liked`, `lineDate` |
| `POST` | `/v2/album/album/posting/modity` | `albumPostingModity` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/posting/delete` | `albumPostingDelete` | `form-urlencoded` | `ids` |
| `POST` | `/v2/album/album/posting/info` | `albumPostingInfo` | `form-urlencoded` | `album_id`, `id` |
| `POST` | `/v2/album/ai/run/now` | `albumAiRunNow` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/clean/status` | `albumAiCleanStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/cluster_detect/options` | `aiClusterDetectOptions` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/progress` | `getAiProgress` | `form-urlencoded` | `types`, `username` |
| `POST` | `/v2/album/ai/rescene` | `albumAiRescene` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/rescene/status` | `albumAiResceneStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/pet/clean/status` | `albumAiPetCleanStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/pet/clean` | `albumAiPetClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/picking/task/create` | `aiPickingTaskCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/picking/task/status` | `aiPickingTaskStatus` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/picking/task/cancel` | `aiPickingTaskCancel` | `form-urlencoded` | `id` |
| `POST` | `/v2/album/ai/picking/task/completed` | `aiPickingTaskCompleted` | `form-urlencoded` | `id` |
| `POST` | `/v2/album/ai/picking/result` | `aiPickingResult` | `form-urlencoded` | - |
| `POST` | `/v2/album/ai/picking/menu_bar` | `aiPickingMenuBar` | `form-urlencoded` | - |
| `POST` | `/v2/album/album/create/batch` | `albumCreateBatch` | `form-urlencoded` | `create_param` |
| `POST` | `/v2/album/ilike/list` | `getIlikeList` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/day/first` | `getDaysFirstList` | `form-urlencoded` | - |
| `POST` | `/transcode/mediainfo/exif` | `postExif` | `form-urlencoded` | `file_path` |
| `GET` | `/transcode/decrypt/mediainfo/exif` | `postDecryptExif` | `query` | `encrypted_path`, `from` |
| `POST` | `/v2/album/album/path/cover` | `getDirCovers` | `form-urlencoded` | `paths` |
| `POST` | `/v2/album/feed/bypath` | `albumForFolderPath` | `form-urlencoded` | `paths` |
| `POST` | `/v2/album/babygrowth/timeline` | `getBabyTimeline` | `form-urlencoded` | - |
| `POST` | `/v2/album/babygrowth/like` | `likeBabyGrowth` | `form-urlencoded` | `album_id`, `liked`, `lineDate` |
| `POST` | `/v2/album/babygrowth/save` | `saveBabyGrowth` | `form-urlencoded` | - |
| `POST` | `/v2/album/babygrowth/info` | `getBabyGrowthinfo` | `form-urlencoded` | `album_id`, `lineDate` |
| `POST` | `/v2/album/babygrowth/setMilestone` | `setMilestone` | `form-urlencoded` | `album_id`, `isMilestone`, `lineDate` |
| `POST` | `/v2/album/babygrowth/delete` | `deleteBabyGrowth` | `form-urlencoded` | `album_id`, `lineDate` |
| `POST` | `/v2/album/babygrowth/comment` | `commentBabyGrowth` | `form-urlencoded` | - |
| `POST` | `/zvideo/settings/load` | `settingsLoad` | `form-urlencoded` | `key` |
| `POST` | `/zvideo/settings/set` | `settingsSet` | `form-urlencoded` | `key`, `value` |
| `POST` | `/v2/album/album/visibleTimeline` | `visibleTimeline` | `form-urlencoded` | - |
| `POST` | `/v2/album/timeline/hide/config` | `getTimelineHideConfig` | `form-urlencoded` | - |
| `POST` | `/v2/album/timeline/hide/config/modify` | `modifyTimelineHideConfig` | `form-urlencoded` | `hidden_album_ids`, `hidden_content_flags`, `hidden_dir_ids` |
| `POST` | `/v2/album/feed/delete` | `removePhoto` | `form-urlencoded` | `feed_ids` |
| `POST` | `/v2/album/rag/search` | `ragSearch` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/index/state` | `ragIndexState` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/flush/clean` | `ragFlushClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/flush/info` | `ragFlushInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/flush/all` | `ragFlushAll` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/dirs/flush` | `ragDirsFlush` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/dirs/delete` | `ragDirsDelete` | `form-urlencoded` | `dir_ids` |
| `POST` | `/v2/album/rag/dirs` | `ragDirs` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/dirs/add` | `ragDirsAdd` | `form-urlencoded` | `paths` |
| `POST` | `/v2/album/rag/datapath/clean` | `ragDatapathClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/datapath/info` | `ragDatapathInfo` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/datapath/set` | `ragDatapathSet` | `form-urlencoded` | `save_path` |
| `POST` | `/v2/album/rag/download/clean` | `ragDownloadClean` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/download/resume` | `ragDownloadResume` | `form-urlencoded` | `id` |
| `POST` | `/v2/album/rag/download/suspend` | `ragDownloadSuspend` | `form-urlencoded` | `id` |
| `POST` | `/v2/album/rag/download/create` | `ragDownloadCreate` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/download/info` | `ragDownloadInfo` | `form-urlencoded` | `model_type` |
| `POST` | `/v2/album/rag/download/check` | `ragDownloadCheck` | `form-urlencoded` | - |
| `POST` | `/v2/album/rag/zplist` | `ragZplist` | `form-urlencoded` | `rag_storage_type` |
| `POST` | `/v2/album/feed/sr/create` | `i` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/sr/status` | `s` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/sr/kill` | `o` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/sr/kill_status` | `c` | `form-urlencoded` | - |
| `POST` | `/v2/album/feed/sr/clean` | `r` | `form-urlencoded` | - |
| `POST` | `/converter/v1/{{t}}` | `l` | `form-urlencoded` | - |
| `GET` | `/converter/v1/{{t}}` | `u` | `query` | - |
| `POST` | `/converter/v1/download/zspool` | `g` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/check` | `p` | `form-urlencoded` | - |
| `POST` | `/converter/v1/engine/check` | `m` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/info` | `f` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/create` | `_` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/resume` | `v` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/suspend` | `S` | `form-urlencoded` | - |
| `POST` | `/converter/v1/download/clean` | `b` | `form-urlencoded` | - |

### 4.7 系统监控、硬件控制 (UPS/风扇/LED)、网络设置、安全防护、虚拟机与账号认证模块 (共 361 个端点)
涵盖 Webpack 模块：
- `74168`：系统状态与设置 (`/system/*`, `/setting/*`)、全盘空间物理画像 (`/system/diskusage3`)、固件升级 (`/upgrade/*`)、硬盘休眠 (`/disk/*`)、UPS 电源管理 (`/ups/*`, `/upsv2/*`)、风扇与 LED 控制 (`/fan/set`, `/led/set`)、IP 访问控制黑白名单 (`/ip_access/*`)、WOL 网络唤醒 (`/wakeol/*`)、设备迁移 (`/migrate/*`)。
- `53733`：网络配置 (`/network-settings/v1/*`：网卡接口、Bond 聚合、Bridge 网桥、无线 WiFi、IPv6、代理、网关排序、蓝牙 AP `/webble/*`)。
- `41718`：账号认证、RSA 公钥、登录设备管理、手机/邮箱验证、极连接 QuickConnect (`/auth/*`, `/account/*`, `/device/*`, `/quick/*`, `/v2/qc/*`, `/sms/*`, `/v2/email/*`)。
- `88175`：防火墙 (`/v1/zfirewall/*`)；`8373`：动态域名 (`/zddns/v1/ddns/*`)；`99499`：SSL 证书 (`/cert/*`)；`20012`：病毒查杀 (`/v1/zvirusscan/*`)；`80149`：KVM 虚拟机 (`/v1/vm/domain/*`)；`81792` & `5600`：应用商店与 OnlyOffice (`/AppStore/v1/*`, `/AppCenter/OnlyOffice/*`)；`8279`：Time Machine (`/tm/*`)。

> **补充系统启停与状态接口 (`app.d3d20628.js` / `52324`)**：
> - `POST /system/reboot`：重启 NAS 设备
> - `POST /system/shutdown`：关闭 NAS 设备
> - `POST /system/polling2`：高频轮询系统状态（CPU/内存/温度/网络速率/任务数/告警）
> - `POST /manager/v2/processes`：获取系统进程列表及 CPU/内存占用
> - `POST /manager/services` & `POST /manager/srvs`：获取系统服务状态列表
> - `POST /manager/srv/start` & `POST /manager/srv/stop`：启动/停止指定系统服务

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `POST` | `/setting/load` | `getShareSetting` | `form-urlencoded` | - |
| `POST` | `/setting/change` | `postSettingChange` | `form-urlencoded` | `boot_on_power_off`, `wallpaper_tip_show` |
| `POST` | `/setting/wallpaper` | `setWallpaper` | `form-urlencoded` | `current`, `path`, `paths` |
| `GET` | `/zhelper/state` | `getRhelpStatus` | `query` | - |
| `POST` | `/action/list` | `getLog` | `form-urlencoded` | `num`, `start_id`, `type` |
| `POST` | `/action/log` | `g` | `form-urlencoded` | - |
| `POST` | `/device/loginlist` | `h` | `form-urlencoded` | - |
| `POST` | `/device/remove` | `removeDevice` | `form-urlencoded` | - |
| `POST` | `/auth/lanonly` | `_` | `form-urlencoded` | - |
| `POST` | `/system/setting` | `v` | `form-urlencoded` | - |
| `POST` | `/file/dw` | `addDw` | `form-urlencoded` | - |
| `POST` | `/v2/file/dwlist` | `dwList` | `form-urlencoded` | `location`, `share_flag` |
| `POST` | `/system/smbstart` | `k` | `form-urlencoded` | - |
| `POST` | `/system/smbstop` | `w` | `form-urlencoded` | - |
| `POST` | `/led/set` | `ledSet` | `form-urlencoded` | - |
| `POST` | `/fan/set` | `fanSet` | `form-urlencoded` | - |
| `POST` | `/disk/sleepset` | `sleepSet` | `form-urlencoded` | - |
| `GET` | `/webble/ap/status` | `getApStatus` | `query` | - |
| `POST` | `/webble/ap/open` | `D` | `form-urlencoded` | - |
| `POST` | `/webble/ap/switch` | `M` | `form-urlencoded` | - |
| `GET` | `/webble/wpa/status` | `getT2WifiStatus` | `query` | - |
| `GET` | `/webble/wpa/start` | `P` | `query` | - |
| `GET` | `/webble/wpa/stop` | `E` | `query` | - |
| `GET` | `/webble/wpa/restart` | `T` | `query` | - |
| `POST` | `/setting/sshd/status` | `getSSHStatus` | `form-urlencoded` | - |
| `POST` | `/setting/sshd/open` | `setSSH` | `form-urlencoded` | - |
| `POST` | `/setting/sshd/close` | `B` | `form-urlencoded` | - |
| `POST` | `/setting/timezones` | `timezones` | `form-urlencoded` | - |
| `POST` | `/setting/timezone/set` | `timezoneSet` | `form-urlencoded` | - |
| `POST` | `/setting/time/sync` | `timeSync` | `form-urlencoded` | - |
| `POST` | `/setting/languages` | `Z` | `form-urlencoded` | - |
| `POST` | `/setting/language/set` | `j` | `form-urlencoded` | - |
| `POST` | `/ip_access/add_black` | `O` | `form-urlencoded` | - |
| `POST` | `/ip_access/update_control` | `U` | `form-urlencoded` | - |
| `POST` | `/ip_access/control_list` | `$` | `form-urlencoded` | - |
| `GET` | `/ip_access/download_list_xlsx` | `W` | `query` | - |
| `POST` | `/ip_access/add_white` | `V` | `form-urlencoded` | - |
| `POST` | `/ip_access/save_limit_config` | `K` | `form-urlencoded` | - |
| `POST` | `/ip_access/get_limit_config` | `q` | `form-urlencoded` | - |
| `POST` | `/wakeol/get_config` | `J` | `form-urlencoded` | - |
| `POST` | `/wakeol/set_config` | `X` | `form-urlencoded` | - |
| `POST` | `/action/new` | `addLog` | `form-urlencoded` | `action`, `failure_num`, `success_num` |
| `POST` | `/action/deleteall` | `delAllLog` | `form-urlencoded` | `type` |
| `POST` | `/action/known` | `knownLog` | `form-urlencoded` | `ids` |
| `POST` | `/v2/share/modify` | `shareUpdate` | `form-urlencoded` | `code`, `state` |
| `POST` | `/v2/share/list` | `getShareList` | `form-urlencoded` | `num`, `size`, `start`, `type` |
| `POST` | `/zhelper/close` | `closeRhelp` | `form-urlencoded` | `password`, `second` |
| `POST` | `/zhelper/open` | `openRhelp` | `form-urlencoded` | `password`, `second` |
| `GET` | `/rhelp/sendsms` | `rhelpSMS` | `query` | - |
| `POST` | `/v2/share/delete` | `clear` | `form-urlencoded` | `ids` |
| `POST` | `/v2/album/share/list` | `getShareAlbumList` | `form-urlencoded` | `num`, `start` |
| `POST` | `/settings` | `doubleNetSetting` | `form-urlencoded` | `bond_mode`, `check_ipv6`, `ipv6`, `net_config`, `wifi_reconnect` |
| `GET` | `/settings?wifi_net_info=true` | `getBondNetInfo` | `query` | - |
| `GET` | `/settings?net_info=true` | `getUnBondNetInfo` | `query` | - |
| `GET` | `/settings?unbond_net_info=true` | `getWifiNetIpv6` | `query` | - |
| `GET` | `/settings?wifi_net_info_6=true` | `getBondNetIpv6` | `query` | - |
| `GET` | `/settings?net_info_6=true` | `getUnBondNetIpv6` | `query` | - |
| `GET` | `/settings?unbond_net_info_6=true` | `checkIsBond` | `query` | - |
| `GET` | `/settings?is_bond=true` | `getIpv6AgingStatus` | `query` | - |
| `GET` | `/settings?show=true` | `setIpv6AgingStatus` | `query` | - |
| `GET` | `/ups/info` | `getUPSInfo` | `query` | - |
| `POST` | `/ups/setting` | `settingUpsTime` | `form-urlencoded` | - |
| `POST` | `/ups/setting_u2600t` | `settingUpsTime_u2600t` | `form-urlencoded` | - |
| `POST` | `/ups/setting_u3500t` | `settingUpsTime_u3500t` | `form-urlencoded` | - |
| `POST` | `/ups/setting_t2sbatt` | `settingUpsTime_t2sbatt` | `form-urlencoded` | - |
| `POST` | `/ups/setting_t6batt` | `settingUpsTime_t6batt` | `form-urlencoded` | - |
| `POST` | `/ups/switch` | `changeUpsSwitch` | `form-urlencoded` | - |
| `POST` | `/sms/hsend` | `rhelpSMS2` | `form-urlencoded (server)` | - |
| `POST` | `/zhelper/nasid` | `getSign` | `form-urlencoded` | - |
| `POST` | `/sms/hcheck` | `getSecond` | `form-urlencoded (server)` | `key_id`, `nasid`, `sms_code`, `username` |
| `POST` | `/setting/pps/load` | `getSettingPps` | `form-urlencoded` | - |
| `POST` | `/setting/pps/set` | `postSettingPps` | `form-urlencoded` | - |
| `POST` | `/system/versions` | `postSystemVersions` | `form-urlencoded` | - |
| `POST` | `/network/diagnose` | `pingBaidu` | `form-urlencoded` | - |
| `POST` | `/v2/tob/share/config/get` | `tobShareConfigGet` | `form-urlencoded` | - |
| `GET` | `"/zspool/external/list")` | `wifiDisconnect` | `query` | - |
| `GET` | `/settings?wifi_disconnect=true` | `getNetStatus` | `query` | - |
| `POST` | `/settings?get_net_status=true` | `smrAlertIgnore` | `form-urlencoded` | `sn` |
| `POST` | `/storagepool/balance/flush/start` | `balanceFlushStart` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/storagepool/balance/flush/status` | `balanceFlushStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/storagepool/balance/task/start` | `balanceTaskStart` | `form-urlencoded` | `gids`, `pid`, `pos`, `type` |
| `POST` | `/storagepool/balance/task/timer` | `balanceTaskTimer` | `form-urlencoded` | - |
| `POST` | `/storagepool/balance/task/status` | `balanceTaskStatus` | `form-urlencoded` | `pid`, `type` |
| `POST` | `/storagepool/balance/task/cancel` | `balanceTaskCancel` | `form-urlencoded` | `pid`, `type` |
| `GET` | `/storagepool/scrub/cancel` | `scrubCancel` | `query` | `pid`, `type` |
| `POST` | `/env/checkVmService` | `virtualMachineCheck` | `form-urlencoded` | - |
| `POST` | `/upgrade/evaccind_status` | `getProtectConfig` | `form-urlencoded` | - |
| `POST` | `/upgrade/evaccind_config` | `setProtectConfig` | `form-urlencoded` | - |
| `POST` | `/disk/sleep_check` | `getDiskSleepStatus` | `form-urlencoded` | - |
| `POST` | `/action/delete` | `delLog` | `form-urlencoded` | `ids`, `type` |
| `POST` | `/upsv2/setting_internal_ups` | `settingInternalUps` | `form-urlencoded` | `timewaittoshutdown` |
| `GET` | `/upsv2/internal_ups_info` | `internalUpsInfo` | `query` | - |
| `POST` | `/upsv2/test_remote_ups_connection` | `testRemoteUpsConnection` | `form-urlencoded` | - |
| `POST` | `/upsv2/config_remote_ups` | `configRemoteUps` | `form-urlencoded` | - |
| `POST` | `/upsv2/switch_nut_server` | `switchNutServer` | `form-urlencoded` | `action` |
| `POST` | `/upsv2/modify_external_ups_mode` | `modifyExternalUpsMode` | `form-urlencoded` | `mode` |
| `POST` | `/upsv2/setting_external_ups` | `settingExternalUps` | `form-urlencoded` | `timewaittoshutdown` |
| `POST` | `/upsv2/switch_external_ups` | `switchExternalUps` | `form-urlencoded` | `action` |
| `GET` | `/upsv2/external_ups_info` | `externalUpsInfo` | `query` | - |
| `POST` | `/network-settings/v1/wireless/config/update` | `updateWirelessConfig` | `application/json` | - |
| `POST` | `/network-settings/v1/wireless/reconnect` | `reconnectWifi` | `application/json` | `name` |
| `POST` | `/network-settings/v1/wireless/enabled` | `enableWireless` | `application/json` | `enabled`, `name` |
| `POST` | `/network-settings/v1/interface/update/bulk` | `sortNet` | `application/json` | - |
| `GET` | `/network-settings/v1/wireless/listadv` | `thirdPartWirelessNetCardList` | `query` | - |
| `POST` | `/network-settings/v1/wireless/connect` | `thirdPartNetCardConnectWifi` | `application/json` | `name`, `password`, `ssid` |
| `POST` | `/network-settings/v1/wireless/disconnect` | `thirdPartNetCardDisconnectWifi` | `application/json` | - |
| `POST` | `/network-settings/v1/wireless/config/delete` | `thirdPartNetCardForgetWifi` | `application/json` | `ssid` |
| `POST` | `/network-settings/v1/wireless/scan` | `thirdPartNetCardScanWifiList` | `application/json` | `name` |
| `POST` | `/network-settings/v1/wireless/config/list` | `thirdPartNetCardGetWifiList` | `form-urlencoded` | `name` |
| `GET` | `/webble/wifi/network/add` | `addWifi` | `query` | `pwd`, `ssid` |
| `GET` | `/webble/wifi/network/select` | `connectWifi` | `query` | `ssid` |
| `GET` | `/webble/wifi/network/remove` | `forgetWifi` | `query` | `ssid` |
| `GET` | `/webble/wifi/network/scan/list` | `scanWifiList` | `query` | - |
| `GET` | `/webble/wifi/network/list` | `getWifiList` | `query` | - |
| `GET` | `/network-settings/v1/network/mode/show` | `getNetworkMode` | `query` | - |
| `GET` | `/network-settings/v1/interface/list` | `getAllInterfaceList` | `query` | - |
| `GET` | `/network-settings/v1/bond/list` | `getBondList` | `query` | - |
| `GET` | `/home/wifi/status` | `C` | `application/json` | - |
| `POST` | `/network-settings/v1/network/mode/update` | `changeNetworkMode` | `application/json` | `mode` |
| `POST` | `/network-settings/v1/interface/show` | `getInterfaceInfo` | `application/json` | - |
| `GET` | `/network-settings/v1/wireless/list` | `getWifiInfo` | `query` | - |
| `POST` | `/network-settings/v1/interface/update` | `updateInterface` | `application/json` | - |
| `POST` | `/network-settings/v1/bond/create` | `createBondMode` | `application/json` | - |
| `POST` | `/network-settings/v1/bond/update` | `updateBondMode` | `application/json` | `mode`, `name` |
| `POST` | `/network-settings/v1/bond/delete` | `deleteBondMode` | `application/json` | `name` |
| `POST` | `/network-settings/v1/bridge/list` | `getAllBridgeList` | `application/json` | - |
| `POST` | `/network-settings/v1/bridge/create` | `createNetBridge` | `application/json` | - |
| `POST` | `/network-settings/v1/bridge/show` | `getNetBridgeInfo` | `application/json` | - |
| `POST` | `/network-settings/v1/bridge/delete` | `deleteNetBridge` | `application/json` | `name` |
| `GET` | `/network-settings/v1/public4/list` | `getV4PublicList` | `query` | - |
| `POST` | `/network-settings/v1/public4/show` | `getV4Public` | `application/json` | `name` |
| `GET` | `/network-settings/v1/public6/list` | `getV6PublicList` | `query` | - |
| `POST` | `/network-settings/v1/public6/show` | `getV6Public` | `application/json` | - |
| `POST` | `/network-settings/v1/public6/check` | `checkV6Status` | `application/json` | `ipv6` |
| `GET` | `/network-settings/v1/ipv6monitor/show` | `getIpv6AgingStatus` | `application/json` | - |
| `POST` | `/network-settings/v1/ipv6monitor/update` | `setIpv6AgingStatus` | `application/json` | - |
| `GET` | `/network-settings/v1/public4/detect` | `public4Detect` | `application/json` | - |
| `GET` | `/network-settings/v1/public6/detect` | `public6Detect` | `application/json` | - |
| `GET` | `/network-settings/` | `getNetworkBaseInfo` | `query` | - |
| `POST` | `/network-settings/v1/network/config/update` | `openThirdPartyNetworkCard` | `application/json` | `advanced` |
| `GET` | `/network-settings/v1/network/config/show` | `thirdPartyNetworkCardStatus` | `query` | - |
| `POST` | `/account/user/list` | `allUserList` | `form-urlencoded` | - |
| `POST` | `/action/report/login` | `reportLogin` | `form-urlencoded` | - |
| `GET` | `/key/pub` | `getPublicKey` | `query` | - |
| `GET` | `o.Tj?"/v2/ud/key/pub":"/key/pub"` | `getPublicKeyV2` | `query` | - |
| `GET` | `/zconn/http/5055/{{t}}` | `l` | `query` | - |
| `POST` | `/zconn/http/5055/{{t}}` | `u` | `form-urlencoded` | - |
| `POST` | `/quick/nasid` | `m` | `form-urlencoded (server)` | - |
| `POST` | `/auth/modremark` | `p` | `form-urlencoded` | - |
| `POST` | `/auth/modnickname` | `g` | `form-urlencoded` | - |
| `POST` | `/quick/modphone` | `h` | `form-urlencoded (server)` | - |
| `POST` | `/auth/login` | `f` | `form-urlencoded` | - |
| `POST` | `/auth/pwdcheck` | `_` | `form-urlencoded` | - |
| `POST` | `/auth/add` | `v` | `form-urlencoded` | - |
| `POST` | `/quick/susername` | `b` | `form-urlencoded (server)` | - |
| `POST` | `/sms/usend` | `y` | `form-urlencoded (server)` | - |
| `POST` | `/sms/send` | `k` | `form-urlencoded (server)` | - |
| `POST` | `/sms/ucheck` | `ucheckSmscode` | `form-urlencoded (server)` | - |
| `POST` | `/v2/email/send` | `A` | `form-urlencoded (email)` | - |
| `POST` | `/v2/email/send/msg` | `sendEmailMsg` | `form-urlencoded (email)` | - |
| `POST` | `/v2/email/modify` | `C` | `form-urlencoded (email)` | - |
| `POST` | `/v2/email/check` | `x` | `form-urlencoded (email)` | - |
| `POST` | `/auth/uaparam` | `D` | `form-urlencoded` | - |
| `POST` | `/auth/upgrade` | `M` | `form-urlencoded` | - |
| `POST` | `/sms/check` | `checkSmscode` | `form-urlencoded (server)` | - |
| `POST` | `/auth/modpassword` | `E` | `form-urlencoded` | - |
| `POST` | `/auth/tmptoken` | `T` | `form-urlencoded` | - |
| `POST` | `/auth/submodify` | `F` | `form-urlencoded` | - |
| `POST` | `/auth/token` | `z` | `form-urlencoded` | - |
| `GET` | `/captcha` | `B` | `query (server)` | - |
| `POST` | `/captcha/check` | `R` | `form-urlencoded (server)` | - |
| `GET` | `/v2/captcha/{{t}}` | `N` | `query (server)` | - |
| `POST` | `/auth/sexists` | `L` | `form-urlencoded` | - |
| `POST` | `/auth/srepassword` | `Z` | `form-urlencoded` | - |
| `POST` | `/account/delete` | `j` | `form-urlencoded` | - |
| `POST` | `/disk/statics` | `O` | `form-urlencoded` | - |
| `POST` | `/auth/authcode` | `U` | `form-urlencoded` | - |
| `POST` | `/setting/sub/perm` | `V` | `form-urlencoded` | - |
| `POST` | `/setting/sub/perm/load` | `Y` | `form-urlencoded` | - |
| `POST` | `/v2/public/user/groups` | `getSubAccountPerm` | `form-urlencoded` | - |
| `POST` | `/auth/modperm` | `Q` | `form-urlencoded` | - |
| `POST` | `/v2/vcode/send/by/email` | `K` | `form-urlencoded (server)` | - |
| `POST` | `/v2/vcode/login` | `q` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/list` | `J` | `form-urlencoded (server)` | - |
| `POST` | `/auth/qcv` | `X` | `form-urlencoded` | - |
| `POST` | `/v2/qc/create/master` | `tt` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/create/sub` | `te` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/list/by/qcv` | `ts` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/mod/state/by/qcv` | `ta` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/del/by/qcv` | `to` | `form-urlencoded (server)` | - |
| `POST` | `/v2/qc/get/active_token` | `ti` | `form-urlencoded (server)` | - |
| `GET` | `/ql/code/gen` | `tn` | `query (server)` | - |
| `POST` | `/ql/code/read` | `tr` | `form-urlencoded (server)` | - |
| `POST` | `/account/loginbyqltk` | `tc` | `form-urlencoded` | - |
| `POST` | `/quick/musername` | `tl` | `form-urlencoded (server)` | - |
| `POST` | `/reset/ok` | `td` | `form-urlencoded` | - |
| `POST` | `/auth/repassword` | `tu` | `form-urlencoded` | - |
| `POST` | `/auth/regist` | `register` | `form-urlencoded` | `afterContentClassName`, `aliases`, `announcementMessage`, `className`, `collapseOnReplaceEdit`, `delaySettingsKey`, `description`, `exclusive`, `extensions`, `fileName`, `firstLineDecorationClassName`, `hasAccessToAllModels` |
| `POST` | `/v1/zfirewall/enable` | `a` | `form-urlencoded` | - |
| `GET` | `/v1/zfirewall/info` | `l` | `query` | - |
| `POST` | `/v1/zfirewall/precheck` | `r` | `form-urlencoded` | - |
| `POST` | `/v1/zfirewall/apply` | `s` | `form-urlencoded` | - |
| `GET` | `/v1/zfirewall/countrys` | `c` | `query` | - |
| `GET` | `/v1/zfirewall/services` | `u` | `query` | - |
| `POST` | `/zddns/v1/ddns/create` | `_` | `application/json` | - |
| `POST` | `/zddns/v1/ddns/update` | `_` | `application/json` | - |
| `GET` | `/zddns/v1/ddns/list` | `init` | `application/json` | `parent` |
| `POST` | `/zddns/v1/ddns/refresh` | `itemRefresh` | `application/json` | - |
| `POST` | `/zddns/v1/ddns/delete` | `-` | `application/json` | - |
| `POST` | `/cert/info` | `getZCertInfo` | `form-urlencoded` | - |
| `POST` | `/cert/apply` | `-` | `multipart/form-data` | - |
| `POST` | `/cert/upload` | `-` | `multipart/form-data` | - |
| `POST` | `/cert/use` | `useCurrentCert` | `form-urlencoded` | - |
| `POST` | `/cert/delete` | `deleteCurrentCert` | `form-urlencoded` | - |
| `POST` | `/cert/regenerate` | `generateCurrentCert` | `form-urlencoded` | - |
| `GET` | `/api/fileshare_service/ssl/reload` | `reloadSSl` | `query` | `status` |
| `POST` | `/cert/backup` | `saveBackupPath` | `form-urlencoded` | - |
| `GET` | `/v1/zvirusscan/database/update` | `databaseUpdate` | `query` | - |
| `POST` | `/v1/zvirusscan/database/upload` | `databaseUpload` | `application/json` | `path_list` |
| `GET` | `/v1/zvirusscan/info` | `getScanInfo` | `query` | - |
| `POST` | `/v1/zvirusscan/scan` | `createScanTask` | `application/json` | `scan_type` |
| `GET` | `/v1/zvirusscan/cancel` | `cancelScanTask` | `query` | - |
| `GET` | `/v1/zvirusscan/suspend` | `suspendScanTask` | `query` | - |
| `GET` | `/v1/zvirusscan/resume` | `resumeScanTask` | `query` | - |
| `GET` | `/v1/zvirusscan/autoscan/info` | `getAutoScanInfo` | `query` | - |
| `POST` | `/v1/zvirusscan/autoscan/create` | `createAutoScanTask` | `application/json` | - |
| `POST` | `/v1/zvirusscan/autoscan/enable` | `enableAutoScanTask` | `application/json` | - |
| `POST` | `/v1/zvirusscan/autoscan/disable` | `disableAutoScanTask` | `application/json` | - |
| `POST` | `/v1/zvirusscan/scanrecord/list?{{n}}` | `getScanRecordList` | `application/json` | - |
| `POST` | `/v1/zvirusscan/scanrecord/delete` | `deleteScanRecord` | `application/json` | - |
| `POST` | `/v1/zvirusscan/scanrecorddetail/list?{{n}}` | `getScanRecordDetail` | `application/json` | `page_index`, `page_size`, `record_uuid` |
| `GET` | `/v1/zvirusscan/scanrecord/summary` | `getScanRecordSummary` | `query` | - |
| `POST` | `/v1/zvirusscan/scanrecorddetail/opt?record_uuid={{t}}` | `scanRecordOpt` | `application/json` | - |
| `POST` | `/v1/zvirusscan/scanrecorddetail/opt?scan_type=realtime` | `rscanRecordOpt` | `application/json` | - |
| `GET` | `/v1/zvirusscan/realtime/enable` | `realtimeEnable` | `query` | - |
| `GET` | `/v1/zvirusscan/realtime/disable` | `realtimeDisable` | `query` | - |
| `GET` | `/v1/zvirusscan/whitelist/list` | `getWhiteList` | `query` | - |
| `POST` | `/v1/zvirusscan/whitelist/add` | `whiteListAdd` | `application/json` | `path_list` |
| `POST` | `/v1/zvirusscan/whitelist/del` | `whiteListDelete` | `application/json` | `path_list` |
| `GET` | `/v1/zvirusscan/whitelist/invalidclear` | `whiteListBatchDel` | `query` | - |
| `GET` | `/v1/zvirusscan/quarantine/detail` | `getQuarantineInfo` | `query` | - |
| `GET` | `/v1/zvirusscan/quarantine/list` | `getQuarantineList` | `query` | - |
| `GET` | `/v1/zvirusscan/quarantine/master` | `setQuarantineMaster` | `query` | `mount_point` |
| `POST` | `/v1/zvirusscan/quarantine/file/list?{{n}}` | `getQuarantineFiles` | `application/json` | - |
| `POST` | `/v1/zvirusscan/quarantine/file/opt` | `quarantineFilesOpt` | `application/json` | `opt`, `qua_files` |
| `GET` | `/v1/zvirusscan/database/clamavinfo` | `getClamavInfo` | `query` | - |
| `GET` | `/v1/zvirusscan/database/info` | `databaseInfo` | `query` | - |
| `GET` | `/v1/zvirusscan/datause/info` | `getDatabaseUseInfo` | `query` | - |
| `GET` | `/v1/zvirusscan/datause/listavailable` | `getDatabasePoolList` | `query` | - |
| `GET` | `/v1/zvirusscan/datause/set` | `setDatabasePool` | `query` | `mount_point` |
| `GET` | `/v1/zvirusscan/setting` | `virusscanGetting` | `query` | - |
| `POST` | `/v1/zvirusscan/setting` | `virusscanSetting` | `application/json` | `default_action`, `scanrecord_keep` |
| `GET` | `/v1/zvirusscan/finish` | `finishTask` | `query` | `record_uuid` |
| `GET` | `/v1/database/update/result/confirm` | `confirmUpdateTask` | `query` | - |
| `POST` | `/v1/vm/domain/checkEnv` | `checkEnv` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/serviceCheck` | `serviceCheck` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/serviceStart` | `serviceStart` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/serviceStop` | `serviceStop` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/getPhyHostStatus` | `getPhyHostStatus` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/getDomainPreLimit` | `getDomainPreLimit` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/snapshotList` | `snapShotList` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/snapshotDelete` | `snapShotDelete` | `form-urlencoded` | `name`, `snapshotName` |
| `POST` | `/v1/vm/domain/snapshotCreate` | `snapShotCreate` | `form-urlencoded` | `name`, `remark` |
| `POST` | `/v1/vm/domain/snapshotRevert` | `snapShotRevert` | `form-urlencoded` | `name`, `snapshotName` |
| `POST` | `/v1/vm/domain/getsnapshotstate` | `snapShotState` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/storageM2List` | `storageM2List` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/sdaList` | `sdaList` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/sdaDelete` | `sdaDelete` | `form-urlencoded` | `name`, `target` |
| `POST` | `/v1/vm/domain/sdaAdd` | `sdaAdd` | `form-urlencoded` | `bus`, `capacity`, `index`, `locate`, `name` |
| `POST` | `/v1/vm/domain/domainCreate` | `vmCreate` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/domainDelete` | `vmDelete` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/domainBaseInfo` | `vmBasicInfo` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/domainDetail` | `vmDetailInfo` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/domainList` | `vmList` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/domainSetStatus` | `vmStatusSet` | `form-urlencoded` | `name`, `status` |
| `POST` | `/v1/vm/domain/domainBaseInfoSave` | `vmBasicInfoSave` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/modifyvncport` | `modifyVNCPort` | `form-urlencoded` | `name`, `port` |
| `POST` | `/v1/vm/domain/export` | `exportVm` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/import` | `importVm` | `application/json` | - |
| `POST` | `/v1/vm/domain/ovf/export` | `exportOvfVm` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/ovf/import` | `importOvfVm` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/ovf/parse` | `parseOvfVm` | `application/json` | `path` |
| `POST` | `/v1/vm/domain/clone` | `cloneVm` | `form-urlencoded` | `clonename`, `name`, `resetMac`, `start` |
| `POST` | `/v1/vm/domain/migrate` | `migrateVm` | `form-urlencoded` | `mountpoint`, `name` |
| `POST` | `/v1/vm/domain/getnetworkconfig` | `getVmNetwork` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/setnetworkconfig` | `setVmNetwork` | `application/json` | - |
| `POST` | `/v1/vm/domain/ismacvlanoneth` | `isMacvlanOnEth` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/isbridgeon` | `isBridgeOn` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/bridgecreate` | `bridgeCreate` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/bridgedelete` | `bridgeDelete` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/getmacaddr` | `getMacAddr` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/isimportmodify` | `isImportFileModify` | `form-urlencoded` | `sourcePath` |
| `POST` | `/v1/vm/domain/domainGetImgType` | `getImgType` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/domainListBySN` | `domainListBySn` | `form-urlencoded` | `sn` |
| `POST` | `/v1/vm/domain/enablevncpasswd` | `setVNCPasswd` | `form-urlencoded` | `name`, `passwd` |
| `POST` | `/v1/vm/domain/isvncpasswdenable` | `isVNCHasPasswd` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/sdaResize` | `sdaResize` | `form-urlencoded` | `capacity`, `diskname`, `index`, `name` |
| `POST` | `/v1/vm/domain/domainGetPauseReason` | `getPauseReason` | `form-urlencoded` | `name` |
| `POST` | `/v1/vm/domain/sriov/sysinfo` | `getSriovInfo` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/sriov/open` | `openSriov` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/sriov/close` | `closeSriov` | `form-urlencoded` | - |
| `POST` | `/v1/vm/domain/sdaModify` | `sdaEdit` | `form-urlencoded` | `bus`, `capacity`, `diskname`, `name` |
| `GET` | `/v1/vm/domain/pci/list` | `getPciDeviceList` | `query` | - |
| `POST` | `/v1/vm/domain/pci/config` | `setVmPci` | `form-urlencoded` | - |
| `GET` | `/v1/vm/domain/timerjob/list` | `getTimer` | `query` | `name` |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/restart":"/AppStore/v1/pkg/restart"` | `n` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/setting/load` | `r` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/setting/save` | `c` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/tags` | `l` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/list` | `d` | `form-urlencoded` | - |
| `POST` | `/zdocker/AppStore/v1/list` | `d` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/info":"/AppStore/v1/pkg/info"` | `u` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/disks` | `m` | `form-urlencoded` | - |
| `POST` | `/zdocker/AppStore/v1/pkg/preinstall` | `p` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/install":"/AppStore/v1/pkg/install"` | `list` | `form-urlencoded` | `path`, `srv_addr` |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/depend/check":"/AppStore/v1/pkg/depend/check"` | `h` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/pkg/depend/by` | `f` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/pkg/tasks` | `_` | `form-urlencoded` | - |
| `POST` | `/zdocker/AppStore/v1/pkg/tasks` | `_` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/task/remove":"/AppStore/v1/pkg/task/remove"` | `v` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/stop":"/AppStore/v1/pkg/stop"` | `b` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/start":"/AppStore/v1/pkg/start"` | `y` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/remove":"/AppStore/v1/pkg/remove"` | `k` | `form-urlencoded` | - |
| `POST` | `t.isDocker?"/zdocker/AppStore/v1/pkg/task/cancel":"/AppStore/v1/pkg/task/cancel"` | `w` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/common/kv/load` | `A` | `form-urlencoded` | - |
| `POST` | `/AppStore/v1/common/kv/save` | `S` | `form-urlencoded` | - |
| `POST` | `/zdocker/AppStore/v1/pkg/cancel_upgrade` | `C` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/service/state` | `o` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/package/latest` | `i` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/disks` | `n` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/package/install` | `r` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/package/install/state` | `c` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/package/install/download/cancel` | `l` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/service/start` | `d` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/service/stop` | `u` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/service/opt/status` | `m` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/service/uninstall` | `p` | `form-urlencoded` | - |
| `POST` | `/AppCenter/OnlyOffice/v1/config/save` | `g` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/font/list` | `h` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/font/save` | `f` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/font/task` | `_` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/font/copy` | `v` | `form-urlencoded` | - |
| `POST` | `/v2/onlyoffice/font/upload` | `b` | `multipart/form-data` | - |
| `POST` | `/v2/onlyoffice/recent/list` | `getRecentList` | `form-urlencoded` | `file_types`, `key_word`, `num`, `start` |
| `POST` | `/v2/onlyoffice/history/list` | `getVersionList` | `form-urlencoded` | `path` |
| `POST` | `/v2/onlyoffice/history/restore` | `restoreFile` | `form-urlencoded` | `dup`, `file_id`, `path` |
| `POST` | `/v2/onlyoffice/recent/remove` | `removeRecentRecord` | `form-urlencoded` | - |
| `POST` | `/themeapp/property/save` | `propertySave` | `form-urlencoded` | `name`, `value` |
| `POST` | `/themeapp/properties` | `properties` | `form-urlencoded` | - |
| `POST` | `/themeapp/property/image/upload` | `propertyImageUpload` | `form-urlencoded` | - |
| `GET` | `/themeapp/property/image/show/{{t}}` | `propertyImageShow` | `query` | - |
| `POST` | `/themeapp/property/batchsave` | `propertyBatchsave` | `application/json` | - |
| `POST` | `/themeapp/property/image/clean` | `propertyImageClean` | `form-urlencoded` | - |
| `POST` | `/themeapp/property/delete` | `propertyDelete` | `form-urlencoded` | `name` |
| `GET` | `/settings?flist=true` | `checkStatus` | `query` | - |
| `POST` | `/tm/open` | `openTM` | `form-urlencoded` | - |
| `POST` | `/tm/close` | `closeTM` | `form-urlencoded` | - |
| `GET` | `/settings?tm_del={{encodeURIComponent(t}}` | `del` | `query` | `ids`, `location` |
| `POST` | `/api/fileshare_service/tm/status` | `tmStatus` | `form-urlencoded` | - |
| `POST` | `/api/fileshare_service/tm/config` | `tmConfig` | `form-urlencoded` | `type` |
| `POST` | `/api/fileshare_service/tm/delete` | `tmDelete` | `form-urlencoded` | - |

### 4.8 极音乐 (ZMusic)、有声读物 (ZAudio)、多网盘生态挂载、备份中心与扩展应用模块 (共 481 个端点)
涵盖其余全部 Webpack API 模块：
- **极音乐与有声读物**：模块 `87857`, `23939` (`/zmusic/api/v1/*`, `/zmusic/api/v2/*`)、模块 `33458` (`/zaudio/api/v1/*`)。
- **第三方网盘挂载与同步**：模块 `2934` (百度网盘 `/znetdisk/*`)、`51899` (阿里云盘 `/zdrive/adrive/*`)、`55834` (115网盘 `/zdrive/yun115/*`)、`95867` (OneDrive `/zonedrive/*`)、`47786` (Google Drive `/zdrive/gdrive/*`)、`86673` (夸克网盘 `/zdrive/kuake/*`)、`66709` (123云盘 `/zdrive/yun123/*`)、`50664` (天翼云盘 `/ztianyiclub/*`)、`10230` (中国移动云盘 `/zdrive/yun139/*`)、`49667` (Dropbox `/zdrive/dropbox/*`)。
- **备份中心与文档同步**：模块 `63932`, `35485` (`/zbakv2/*`)、`41241` (`/zbakcenter/*`)、`37138` (`/v2/crossdevice/backup/*`)、`38026` (`/docsync/*`)、`12684` (`/weixin_backup/*`)。
- **监控中心、邮件备份与极课堂**：模块 `88861` (监控中心 `/AppStore/znvr/v1/camera/*`)、`14318` (邮件管理 `/AppStore/v1/mailmanager/*`)、`41692` (极课堂 `/zedu/*`)、`64681` (NVIDIA 驱动 `/AppStore/NVIDIAdriver/*`)、`11458` (文件救援 `/AppStore/filerescue/list`)。

| HTTP 方法 | API 路由路径 (Endpoint) | 前端方法名 (Function) | 请求格式 / Target | 常见业务参数 (推导自调用点) |
|---|---|---|---|---|
| `GET` | `https://wxrobot.zenithspace.net/wxrobot/robot/list` | `getRobotList` | `query (direct)` | - |
| `POST` | `/AppStore/wxrobot/config/dirinfo` | `getBackupDir` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/config/dirsave` | `setBackupDir` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/chat/list` | `getChatList` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/chat/delete` | `deleteChatMessage` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/chat/save_doc` | `saveTextToDoc` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/weixin/bindlist` | `getBindAccountList` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/weixin/unbind` | `unbindWechat` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/code/bind` | `getBindCode` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/config/all` | `getConfigAll` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/config/set` | `setConfigByKey` | `form-urlencoded` | - |
| `POST` | `/AppStore/wxrobot/pdf/encode` | `pdfEncode` | `form-urlencoded` | `msg_id` |
| `POST` | `/znetdisk/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/znetdisk/file/list` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/znetdisk/auth/token` | `login` | `form-urlencoded` | `auth_code`, `passwd`, `srv_addr`, `user` |
| `POST` | `/znetdisk/auth/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/znetdisk/file/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/znetdisk/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/znetdisk/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/znetdisk/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/znetdisk/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/znetdisk/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/znetdisk/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/znetdisk/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/znetdisk/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/znetdisk/sync/add` | `addAutoList` | `form-urlencoded` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/znetdisk/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/znetdisk/file/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/znetdisk/autobackup/info` | `getAutoBackupInfo` | `form-urlencoded` | - |
| `POST` | `/znetdisk/autobackup/add` | `autoUploadTaskAdd` | `form-urlencoded` | - |
| `POST` | `/znetdisk/autobackup/delete` | `autoUploadTaskDel` | `form-urlencoded` | - |
| `POST` | `/znetdisk/autobackup/start` | `autoUploadTaskStart` | `form-urlencoded` | - |
| `POST` | `/znetdisk/autobackup/stop` | `autoUploadTaskPause` | `form-urlencoded` | - |
| `POST` | `/znetdisk/file/newdir` | `createNewBaiduDir` | `form-urlencoded` | `dir_name`, `path` |
| `POST` | `/znetdisk/autobackup/faillist` | `autoUploadFailList` | `form-urlencoded` | `limit`, `page` |
| `POST` | `/znetdisk/autobackup/clear_fail_files` | `clearFailFiles` | `form-urlencoded` | `file_ids` |
| `POST` | `/znetdisk/membership/active` | `activeMember` | `form-urlencoded` | `code` |
| `POST` | `/znetdisk/share/filelist` | `getBaiduShareList` | `form-urlencoded` | `limit`, `page`, `path`, `short_url`, `spwd` |
| `POST` | `/znetdisk/share/verify` | `checkBaiduLinkPwd` | `form-urlencoded` | `pwd`, `short_url` |
| `POST` | `/znetdisk/share/transfer` | `startTransferTask` | `form-urlencoded` | - |
| `POST` | `/znetdisk/share/transfer_result` | `getTransferProgress` | `form-urlencoded` | `task_id` |
| `POST` | `/znetdisk/order/get_cashier` | `getBuyVipUrl` | `form-urlencoded` | - |
| `POST` | `/zdrive/baidu/rclone/mountinfo` | `getMountInfo` | `form-urlencoded` | - |
| `POST` | `/znetdisk/order/check_free_vip` | `checkFreeVip` | `form-urlencoded` | - |
| `POST` | `/znetdisk/order/direct_charge` | `directChargeVip` | `form-urlencoded` | - |
| `POST` | `/kv/create` | `kvCreate` | `form-urlencoded` | `k`, `v` |
| `POST` | `/kv/load` | `kvLoad` | `form-urlencoded` | `k` |
| `POST` | `/kv/delete` | `kvDelete` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun139/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/yun139/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun139/servicediskinfov2` | `getSpaceInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun139/userbenefits` | `getUserbenefit` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun139/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/yun139/file/contentinfo` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun139/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/yun139/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/yun139/task/download` | `addTask` | `application/json` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/yun139/task/upload` | `upload` | `application/json` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zdrive/yun139/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/yun139/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `GET` | `/AppStore/filerescue/list` | `n` | `query` | - |
| `POST` | `/weixin_backup/rpc/getBindWXUrl` | `openWXBackup` | `form-urlencoded` | - |
| `GET` | `/AppStore/v1/mailmanager/setting` | `o` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/app/reset-all` | `u` | `form-urlencoded` | - |
| `PUT` | `/AppStore/v1/mailmanager/setting/default-store-path` | `i` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/setting/migration/status` | `c` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/setting/migration/results` | `p` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/setting/migration/cancel` | `m` | `form-urlencoded` | - |
| `GET` | `/AppStore/v1/mailmanager/statistics` | `l` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/server-config` | `s` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/test-connection` | `h` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/oauth/providers` | `f` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/oauth/start` | `d` | `application/json` | - |
| `POST` | `/AppStore/v1/mailmanager/oauth/zconnect` | `g` | `form-urlencoded` | - |
| `GET` | `/AppStore/v1/mailmanager/oauth/status` | `v` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/account/oauth` | `S` | `application/json` | - |
| `POST` | `/AppStore/v1/mailmanager/account` | `A` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/account/list` | `Z` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/account/{{t}}` | `C` | `query` | - |
| `PUT` | `/AppStore/v1/mailmanager/account/{{t}}` | `y` | `application/json` | - |
| `DELETE` | `/AppStore/v1/mailmanager/account/{{t}}` | `T` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/fetch/start` | `w` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/fetch/status` | `$` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/fetch/folder-configs` | `z` | `query` | - |
| `PUT` | `/AppStore/v1/mailmanager/fetch/folder-status` | `U` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/folders` | `x` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/list` | `G` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/{{t}}` | `Q` | `query` | - |
| `POST` | `/AppStore/v1/mailmanager/mail/delete` | `q` | `application/json` | - |
| `POST` | `/AppStore/v1/mailmanager/mail/restore` | `B` | `application/json` | - |
| `POST` | `/AppStore/v1/mailmanager/mail/delete-permanent` | `E` | `application/json` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/attachment/preview` | `F` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/attachment/save` | `H` | `query` | - |
| `GET` | `/AppStore/v1/mailmanager/mail/attachment/save-status` | `I` | `query` | - |
| `POST` | `/zmusic/api/v2/playlist/add` | `addPlayList` | `form-urlencoded` | `playlist_name` |
| `GET` | `/zmusic/api/v2/playlist/list` | `playList` | `query` | `order`, `sort_field` |
| `POST` | `/zmusic/api/v2/playlist/update` | `editPlayList` | `form-urlencoded` | `playlist_id`, `playlist_name` |
| `POST` | `/zmusic/api/v2/playlist/delete` | `deletePlayList` | `form-urlencoded` | `playlist_id` |
| `GET` | `/zmusic/api/v2/playlist/info` | `playListInfo` | `query` | `playlist_id` |
| `GET` | `/zmusic/api/v2/playlist/record/list` | `songsList` | `query` | `order`, `page`, `page_size`, `playlist_id`, `song_id` |
| `POST` | `/zmusic/api/v2/playlist/record/add` | `playlistRecordAdd` | `form-urlencoded` | `add_type`, `add_value`, `playlist_id` |
| `GET` | `/zmusic/api/v1/task/status/add` | `getAddStatus` | `query` | `playlist_id` |
| `POST` | `/zmusic/api/v2/playlist/record/remove` | `deleteSongs` | `form-urlencoded` | `playlist_id`, `track_id` |
| `POST` | `/zmusic/api/v1/playlist/dir/delete` | `deleteDir` | `form-urlencoded` | `dirs`, `playlist_id` |
| `GET` | `/zmusic/api/v2/history/record/latest` | `getLastSong` | `query` | `playlist_id` |
| `GET` | `/zmusic/api/v2/song/info/cue` | `getCue` | `query` | `song_id` |
| `POST` | `/zmusic/api/v2/play/random` | `getRandomList` | `form-urlencoded` | `order`, `play_type`, `play_value`, `playlist_id` |
| `POST` | `/zmusic/api/v2/history` | `updateSongsHistory` | `form-urlencoded` | `playlist_id`, `track_id`, `track_progress` |
| `GET` | `/zmusic/api/v1/task/refresh/status` | `getRefreshStatus` | `query` | `playlist_id`, `subConfId`, `updateType` |
| `POST` | `/zmusic/api/v1/task/refresh` | `addRefreshTask` | `form-urlencoded` | `playlist_id` |
| `GET` | `/zmusic/api/v1/playlist/extdev/status` | `checkPlaylistExtdevStatus` | `query` | `playlist_id` |
| `GET` | `/zmusic/api/v2/song/info/lyrics` | `getSongLyrics` | `query` | `song_id` |
| `POST` | `/zmusic/api/v2/song/info/lyrics` | `editSongLyrics` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/song/info/tags` | `editSongTags` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/song/info` | `getSong` | `query` | `song_id` |
| `GET` | `/home` | `message` | `query` | - |
| `GET` | `/home/safe/security?nav=3` | `forbidOutside` | `query` | - |
| `GET` | `/zrps/api/speed/action` | `eL` | `query` | - |
| `GET` | `/zrps/api/speed/cancel` | `eZ` | `query` | - |
| `GET` | `/zrps/api/speed/progress` | `ej` | `query` | - |
| `GET` | `/zrps/api/speed/report` | `eO` | `query` | - |
| `GET` | `/zrps/api/speed/query` | `eU` | `query` | - |
| `POST` | `/zaudio/api/v1/playlist/add` | `addPlayList` | `form-urlencoded` | `playlist_name` |
| `GET` | `/zaudio/api/v1/playlist/all` | `playList` | `query` | `order`, `sort_field` |
| `POST` | `/zaudio/api/v1/playlist/edit` | `editPlayList` | `form-urlencoded` | `playlist_id`, `playlist_name` |
| `POST` | `/zaudio/api/v1/playlist/delete` | `deletePlayList` | `form-urlencoded` | `playlist_id` |
| `GET` | `/zaudio/api/v1/playlist` | `playListInfo` | `query` | `playlist_id` |
| `GET` | `/zaudio/api/v1/playlist/record/list` | `songsList` | `query` | `order`, `page`, `page_size`, `playlist_id`, `song_id` |
| `POST` | `/zaudio/api/v1/playlist/dir/add` | `addDir` | `form-urlencoded` | `dirs`, `playlist_id` |
| `GET` | `/zaudio/api/v1/task/status/add` | `getAddStatus` | `query` | `playlist_id` |
| `POST` | `/zaudio/api/v1/playlist/record/delete` | `deleteSongs` | `form-urlencoded` | `playlist_id`, `track_id` |
| `POST` | `/zaudio/api/v1/playlist/dir/delete` | `deleteDir` | `form-urlencoded` | `dirs`, `playlist_id` |
| `GET` | `/zaudio/api/v1/history/latest` | `getLastSong` | `query` | `playlist_id` |
| `GET` | `/zaudio/api/v1/song/parse/cue` | `getCue` | `query` | `song_id` |
| `GET` | `/zaudio/api/v1/playlist/record/random` | `getRandomList` | `query` | `order`, `play_type`, `play_value`, `playlist_id` |
| `POST` | `/zaudio/api/v1/history` | `updateSongsHistory` | `form-urlencoded` | `playlist_id`, `track_id`, `track_progress` |
| `GET` | `/zaudio/api/v1/playlist/record/position` | `getPlaylistRecordPosition` | `query` | - |
| `GET` | `/zaudio/api/v1/task/refresh/status` | `getRefreshStatus` | `query` | `playlist_id`, `subConfId`, `updateType` |
| `POST` | `/zaudio/api/v1/task/refresh` | `addRefreshTask` | `form-urlencoded` | `playlist_id` |
| `GET` | `/zaudio/api/v1/playlist/extdev/status` | `checkPlaylistExtdevStatus` | `query` | `playlist_id` |
| `POST` | `/zbakv2/task/update` | `n` | `application/json;charset=UTF-8` | - |
| `POST` | `/zbakv2/task/add` | `n` | `application/json;charset=UTF-8` | - |
| `POST` | `/zbakv2/task/list` | `i` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/info` | `s` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/taskstart` | `a` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/planstop` | `r` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/planstart` | `u` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/delete` | `c` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/failist` | `l` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/encpasswd` | `d` | `form-urlencoded` | - |
| `POST` | `/zbakv2/task/dowload_encpasswd` | `g` | `form-urlencoded` | - |
| `GET` | `{{d}}/v2/ud/key/pub` | `h` | `query (direct)` | - |
| `POST` | `{{d}}/v2/ud/agree` | `rid` | `form-urlencoded (direct)` | - |
| `POST` | `{{d}}/v2/ud/expire` | `_` | `form-urlencoded (direct)` | - |
| `POST` | `{{d}}/v2/ud/check` | `v` | `form-urlencoded (direct)` | - |
| `POST` | `/v2/crossdevice/backup/add` | `r` | `form-urlencoded` | - |
| `POST` | `/v2/crossdevice/backup/delete` | `u` | `form-urlencoded` | - |
| `POST` | `/v2/crossdevice/backup/update` | `_` | `form-urlencoded` | - |
| `POST` | `/v2/crossdevice/backup/list` | `d` | `form-urlencoded` | - |
| `POST` | `/v2/crossdevice/backup/start` | `p` | `form-urlencoded` | - |
| `POST` | `/zrps/api/remoteaccess/add` | `whiteList` | `application/json` | `whitelist` |
| `GET` | `/docsync/info` | `getDbInfo` | `query` | - |
| `POST` | `/docsync/setting` | `setDbInfo` | `form-urlencoded` | `databasepath` |
| `GET` | `/home/syncApi/backupConfig` | `backupConfig` | `query` | - |
| `GET` | `/home/syncApi/checkConfig` | `checkConfig` | `query` | - |
| `GET` | `/home/syncApi/restoreConfig` | `restoreConfig` | `query` | - |
| `GET` | `/home/syncApi/checkDatabase` | `checkDatabase` | `query` | - |
| `GET` | `/home/syncApi/restoreDatabase` | `restoreDatabase` | `query` | - |
| `GET` | `/home/syncApi/checkStoragePool` | `checkStoragePool` | `query` | - |
| `POST` | `/zbakcenter/resource/list` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zbakcenter/resource/add` | `add` | `form-urlencoded` | `action`, `group`, `id`, `index`, `not_scrape` |
| `POST` | `/zbakcenter/resource/update` | `update` | `form-urlencoded` | `binding`, `cached`, `el`, `error`, `loading`, `played`, `src`, `vnode` |
| `POST` | `/zbakcenter/resource/delete` | `deleteRes` | `form-urlencoded` | - |
| `POST` | `/zbakcenter/resource/info` | `info` | `form-urlencoded` | `id`, `location` |
| `POST` | `/zedu/v2/type/search_all` | `classification` | `form-urlencoded` | - |
| `POST` | `/zedu/type/add` | `addClassification` | `form-urlencoded` | `parent_type_id`, `type_name` |
| `POST` | `/zedu/v2/tag/search_list` | `getTags` | `form-urlencoded` | `tag_type` |
| `POST` | `/zedu/tag/add` | `createTags` | `form-urlencoded` | `tag_name`, `tag_type` |
| `POST` | `/zedu/user/all` | `getUserList` | `form-urlencoded` | - |
| `POST` | `/zedu/course/create` | `addClass` | `application/json` | `location`, `name`, `parent_id` |
| `POST` | `/zedu/user/studylist` | `recentList` | `form-urlencoded` | `num`, `start` |
| `POST` | `/zedu/course/list` | `courseList` | `form-urlencoded` | - |
| `POST` | `/zedu/course/info` | `courseDetail` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/feeds/list` | `courseFileList` | `form-urlencoded` | - |
| `POST` | `/zedu/proxy/file/list` | `courseDirList` | `form-urlencoded` | - |
| `POST` | `/zedu/feeds/last` | `courseRecord` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/resource/add` | `addContent` | `form-urlencoded` | `classification_id`, `course_id`, `dir_path`, `file_path` |
| `POST` | `/zedu/user/likefilelist` | `courseLikeFile` | `form-urlencoded` | - |
| `POST` | `/zedu/user/likefile` | `likeFile` | `form-urlencoded` | `course_id`, `feed_id` |
| `POST` | `/zedu/user/unlikefile` | `unlikeFile` | `form-urlencoded` | `course_id`, `feed_id` |
| `POST` | `/zedu/resource/list` | `courseDirs` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/course/update` | `changeCourseInfo` | `application/json` | - |
| `POST` | `/zedu/resource/flush` | `flush` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/resource/flushinfo` | `flushProcess` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/feeds/remove` | `remove` | `form-urlencoded` | `id`, `paths`, `skip_bin` |
| `POST` | `/zedu/course/note/save` | `saveNote` | `form-urlencoded` | `content`, `course_id` |
| `POST` | `/zedu/course/note/info` | `getNote` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/feeds/delete` | `deleteFile` | `form-urlencoded` | `course_id`, `encrypted_path` |
| `POST` | `/zedu/course/delete` | `deleteCourse` | `form-urlencoded` | `course_id` |
| `POST` | `/zedu/user/study` | `recordUpdate` | `form-urlencoded` | `course_id`, `feed_id`, `second` |
| `POST` | `/zedu/type/delete` | `deleteClassification` | `form-urlencoded` | `type_id` |
| `POST` | `/zedu/tag/delete` | `deleteTag` | `form-urlencoded` | `tag_name`, `tag_type` |
| `POST` | `/apps/homeapps` | `homeapps` | `form-urlencoded` | `os` |
| `POST` | `/apps/homeappsmobile` | `homeappsmobile` | `form-urlencoded` | `os` |
| `POST` | `/apps/savehomeappsmobile` | `savehomeappsmobile` | `form-urlencoded` | - |
| `POST` | `/apps/allapps` | `allapps` | `form-urlencoded` | - |
| `POST` | `/apps/savehomeapps` | `savehomeapps` | `form-urlencoded` | `app_ids` |
| `POST` | `/shortcut/homeshortcut` | `homeshortcut` | `form-urlencoded` | - |
| `POST` | `/shortcut/dockershortcuts` | `dockerShortcut` | `form-urlencoded` | - |
| `POST` | `/shortcut/deleteshortcut` | `deleteshortcut` | `form-urlencoded` | - |
| `POST` | `/shortcut/deletedockershortcut` | `deleteDockerShortcut` | `form-urlencoded` | - |
| `POST` | `/shortcut/renameshortcut` | `renameshortcut` | `form-urlencoded` | - |
| `POST` | `/shortcut/addshortcut` | `addshortcut` | `form-urlencoded` | `file_path` |
| `GET` | `/thunder/getWebUrl` | `getThunderUrl` | `query` | - |
| `POST` | `/shortcut/adddockershortcut` | `adddockershortcut` | `form-urlencoded` | `link`, `name` |
| `POST` | `/shortcut/renamedockershortcut` | `renameDockerShortcut` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/autobackup/add` | `autoUploadTaskAdd` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/autobackup/start` | `autoUploadTaskStart` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/auth/zconnect` | `auth` | `form-urlencoded` | `nid`, `t`, `uuid` |
| `POST` | `/zdrive/gdrive/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/gdrive/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/gdrive/file/filee` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/spaceinfo` | `getSpaceInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/driveinfo` | `getDriveInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/gdrive/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/gdrive/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/gdrive/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/gdrive/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/sync/add` | `addAutoList` | `application/json` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zdrive/gdrive/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zdrive/gdrive/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zdrive/gdrive/autobackup/info` | `getAutoBackupInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/autobackup/delete` | `autoUploadTaskDel` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/autobackup/stop` | `autoUploadTaskPause` | `form-urlencoded` | - |
| `POST` | `/zdrive/gdrive/autobackup/faillist` | `autoUploadFailList` | `form-urlencoded` | `limit`, `page` |
| `POST` | `/zdrive/gdrive/autobackup/clear_fail_files` | `clearFailFiles` | `form-urlencoded` | `file_ids` |
| `POST` | `/zdrive/dropbox/auth/check` | `T` | `form-urlencoded` | - |
| `POST` | `/zdrive/dropbox/auth/zconnect` | `init` | `form-urlencoded` | `parent` |
| `POST` | `/zdrive/dropbox/userinfo` | `getDropboxUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/dropbox/auth/logout` | `switchAccount` | `form-urlencoded` | - |
| `POST` | `/zdrive/dropbox/rclone/mountinfo` | `-` | `form-urlencoded` | - |
| `POST` | `/upgrade/external/rclone/zspace/mountinfo` | `handleZspaceMessage` | `form-urlencoded` | - |
| `POST` | `https://api.zenithspace.net/v99/ai/xkasdgwe` | `o` | `form-urlencoded (direct)` | - |
| `POST` | `/ztianyiclub/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/ztianyiclub/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/ztianyiclub/auth/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/ztianyiclub/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/ztianyiclub/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/ztianyiclub/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/ztianyiclub/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/sync/add` | `addAutoList` | `form-urlencoded` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/ztianyiclub/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/ztianyiclub/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/ztianyiclub/auth/userlevel` | `userVipLevel` | `form-urlencoded` | - |
| `POST` | `/ztianyiclub/auth/buymember` | `buyLink` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/adrive/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/driveinfo` | `getDriveInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/spaceinfo` | `getSpaceInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/vipinfo` | `getVipInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/adrive/file/file` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/adrive/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/adrive/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/adrive/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zdrive/adrive/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/adrive/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/sync/add` | `addAutoList` | `application/json` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zdrive/adrive/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zdrive/adrive/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zdrive/yun115/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/yun115/auth/qrinfo` | `getQrCode` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/auth/qrstatus` | `getScanLoginStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/auth/access_token` | `accessToken` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/yun115/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/yun115/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/yun115/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/yun115/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/yun115/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun115/sync/add` | `addAutoList` | `application/json` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zdrive/yun115/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zdrive/yun115/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/AiLab/v1/llama/state` | `aiLlamaState` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/disks` | `aiDisks` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/models` | `aiModels` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/model/download/new` | `aiDownloadModel` | `form-urlencoded` | `code`, `mp` |
| `POST` | `/AiLab/v1/model/download/state` | `aiDownloadState` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/model/download/cancel` | `aiDownloadCancel` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/llama/start` | `aiLlamaStart` | `form-urlencoded` | `code`, `device` |
| `POST` | `/AiLab/v1/llama/stop` | `aiLlamaStop` | `form-urlencoded` | - |
| `POST` | `/AiLab/v1/model/clean` | `aiModelClean` | `form-urlencoded` | `code` |
| `POST` | `/zbakv2/plugin/v2/setting/type/commonrule/update` | `updateCommonRule` | `application/json;charset=UTF-8` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/customrule/add` | `addCustomRule` | `application/json;charset=UTF-8` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/customrule/update` | `updateCustomRule` | `application/json;charset=UTF-8` | - |
| `POST` | `/zbakv2/plugin/v2/task/plugin_start` | `pluginStartTasks` | `form-urlencoded` | `plugin_type` |
| `POST` | `/zbakv2/plugin/v2/task/list` | `taskListV2` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/task/cancel` | `cancelTaskV2` | `form-urlencoded` | `task_id` |
| `POST` | `/zbakv2/plugin/v2/task/delete` | `deleteTaskV2` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/task/retry` | `retryTaskV2` | `form-urlencoded` | `task_id` |
| `POST` | `/zbakv2/plugin/v2/task/recent_list` | `recentTaskList` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/task/failist` | `taskFailFiles` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/msg_device/detect` | `detectPluginMsgDevice` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/msg_device/clear` | `clearPluginMsgDevice` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/msg_device/task_start` | `startTaskMsgDevice` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/setting/info` | `h` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/list` | `pluginTypeList` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/info` | `pluginTypeInfo` | `form-urlencoded` | `plugin_type` |
| `POST` | `/zbakv2/plugin/v2/setting/type/enable` | `enableType` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/disable` | `disableType` | `form-urlencoded` | - |
| `POST` | `/zbakv2/plugin/v2/setting/type/ruletype/change` | `changeRuleType` | `form-urlencoded` | `plugin_type`, `rule_type` |
| `POST` | `/zbakv2/plugin/v2/setting/type/customrule/delete` | `deleteCustomRule` | `form-urlencoded` | `plugin_type`, `rule_id` |
| `POST` | `/zbakv2/plugin/v2/setting/type/customrule/enable` | `enableCustomRule` | `form-urlencoded` | `plugin_type`, `rule_id` |
| `POST` | `/zbakv2/plugin/v2/setting/type/customrule/disable` | `disableCustomRule` | `form-urlencoded` | `plugin_type`, `rule_id` |
| `GET` | `/home/album/setup?active=4` | `hanldeSrStatusError` | `query` | - |
| `GET` | `/AppStore/NVIDIAdriver/gpu/info` | `s` | `query` | - |
| `GET` | `/AppStore/NVIDIAdriver/gpu/unload` | `r` | `query` | - |
| `GET` | `/AppStore/NVIDIAdriver/gpu/task` | `o` | `query` | - |
| `POST` | `/zdrive/yun123/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/yun123/file/file` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/yun123/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/rclone/mountinfo` | `getMountInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/yun123/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/yun123/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/yun123/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/yun123/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zdrive/yun123/sync/add` | `addAutoList` | `application/json` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zdrive/yun123/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zdrive/yun123/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zdrive/kuake/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zdrive/kuake/file/file` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zdrive/kuake/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/userlevelinfo` | `getUserLevelInfo` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zdrive/kuake/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zdrive/kuake/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zdrive/kuake/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zdrive/kuake/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zdrive/kuake/sync/add` | `addAutoList` | `application/json` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zdrive/kuake/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zdrive/kuake/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zmusic/api/v2/song/search/lyrics` | `a` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/song/scrape/lyrics` | `r` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/song/save/lyrics` | `c` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/setting` | `setScrapeLyric` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/setting/load` | `getScrapeLyricSet` | `query` | - |
| `GET` | `/zmusic/api/v2/song/overview` | `songOverview` | `query` | - |
| `POST` | `/zmusic/api/v2/song/file/add` | `songFileAdd` | `form-urlencoded` | `file_path` |
| `GET` | `/zmusic/api/v2/genre/list` | `genreList` | `query` | - |
| `GET` | `/zmusic/api/v2/album/list` | `albumList` | `query` | `filter_type`, `filter_value`, `order`, `page`, `page_size`, `sort_field` |
| `GET` | `/zmusic/api/v2/folder/list` | `folderList` | `query` | - |
| `POST` | `/zmusic/api/v2/folder/level/list` | `folderLevelList` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/artist/list` | `artistList` | `query` | - |
| `GET` | `/zmusic/api/v2/artist/index` | `artistIndex` | `query` | `artist_index` |
| `POST` | `/zmusic/api/v2/song/list` | `songList` | `form-urlencoded` | `filter_type`, `filter_value`, `order`, `page`, `page_size`, `song_id`, `sort_field` |
| `GET` | `/zmusic/api/v2/album/info` | `albumInfo` | `query` | `album_id` |
| `POST` | `/zmusic/api/v2/song/remove` | `songRemove` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/play/list` | `createPlayList` | `form-urlencoded` | `fullPath`, `pageFilterValue`, `pageType`, `playMusicFromHistoryPlayList`, `playlist_type` |
| `GET` | `/zmusic/api/v2/history/song` | `historyList` | `query` | `location`, `notepad_id`, `num`, `start` |
| `POST` | `/zmusic/api/v2/history/record/clear` | `historyClear` | `form-urlencoded` | `history_type` |
| `POST` | `/zmusic/api/v2/history/record/delete` | `historyDelete` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/playlist/remove` | `playlistRemove` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/song/file/refresh` | `songFileRefresh` | `form-urlencoded` | `file_path` |
| `GET` | `/zmusic/api/v2/services/status` | `servicesStatus` | `query` | - |
| `GET` | `/zmusic/api/v2/task/status` | `taskStatus` | `query` | - |
| `GET` | `/zmusic/api/v2/song/file` | `songFile` | `query` | - |
| `POST` | `/zmusic/api/v2/file/info/lyrics` | `fileInfoLyrics` | `form-urlencoded` | `file_path` |
| `POST` | `/zmusic/api/v2/file/info` | `fileInfo` | `form-urlencoded` | `file_path`, `path` |
| `POST` | `/zmusic/local/share/playlist/decrypt` | `sharePlaylistDecrypt` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/share/playlist/record/list` | `sharePlaylistRecordList` | `query` | - |
| `GET` | `/zmusic/api/v2/share/playlist/info` | `sharePlaylistInfo` | `query` | `playlist_id` |
| `GET` | `/zmusic/api/v2/share/playlist/list` | `sharePlaylistList` | `query` | `order`, `sort_field` |
| `POST` | `/zmusic/api/v2/playlist/share/set` | `playlistShareSet` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/history/record/add` | `updateSongsHistory` | `form-urlencoded` | `playlist_id`, `track_id`, `track_progress` |
| `GET` | `/zmusic/api/v2/history/album` | `historyAlbum` | `query` | - |
| `GET` | `/zmusic/api/v2/history/playlist` | `historyPlaylist` | `query` | - |
| `POST` | `/zmusic/api/v2/song/scrape/lyrics/set` | `setScrapeStatus` | `form-urlencoded` | `scrape`, `song_id` |
| `POST` | `/zmusic/api/v2/favorite/add` | `favoriteAdd` | `form-urlencoded` | - |
| `POST` | `/zmusic/api/v2/favorite/delete` | `favoriteDelete` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/favorite/song/list` | `favoriteSongList` | `query` | - |
| `GET` | `/zmusic/api/v2/favorite/album/list` | `favoriteAlbumList` | `query` | - |
| `GET` | `/zmusic/api/v2/favorite/artist/list` | `favoriteArtistList` | `query` | - |
| `GET` | `/zmusic/api/v2/favorite/playlist/list` | `favoritePlaylistList` | `query` | - |
| `GET` | `/zmusic/api/v2/song/latest` | `songLatest` | `query` | `limit` |
| `GET` | `/zmusic/api/v2/favorite/list` | `favoriteList` | `query` | `limit` |
| `GET` | `/zmusic/api/v2/picks/overview` | `picksOverview` | `query` | `refresh` |
| `GET` | `/zmusic/api/v2/picks` | `picks` | `query` | `picks_type` |
| `POST` | `/zmusic/api/v2/task/cancel` | `taskCancel` | `form-urlencoded` | - |
| `GET` | `/zmusic/api/v2/subsonic/account` | `subsonicAccountGet` | `query` | - |
| `POST` | `/zmusic/api/v2/subsonic/account` | `subsonicAccountSet` | `form-urlencoded` | `enabled`, `password`, `username` |
| `POST` | `/AppStore/znvr/v1/camera/manage/enable` | `startCamera` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/manage/disable` | `stopCamera` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/manage/disconnection` | `disconnectCamera` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/config` | `cameraConfig` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/verify` | `cameraVerify` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/add/init` | `addCameraInit` | `form-urlencoded` | `cam_list` |
| `POST` | `/AppStore/znvr/v1/camera/add/save` | `addCameraSave` | `form-urlencoded` | `cam_list` |
| `POST` | `/AppStore/znvr/v1/camera/config/save` | `cameraFeatureSave` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/info/snapshot` | `getCameraSnapshot` | `form-urlencoded` | `cam_https`, `cam_port`, `cam_pwd`, `cam_type`, `cam_username`, `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/manage/list` | `getCameraList` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/manage/use` | `getCameraManageUse` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/timetree/top` | `getDateTimeTree` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/camtree/top` | `getCameraTree` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/camtree/all` | `getRecordList` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/find/all` | `getAllRecordList` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/find/date` | `getRecordByDate` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/find/uuid` | `getRecordByCamera` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/capacity/info` | `getCapacityInfo` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/record/delete` | `delRecordFile` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/info/streams` | `getCameraStreams` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/camera/info/timeline3` | `getCameraTimeline` | `form-urlencoded` | `cam_uuid`, `end`, `start` |
| `POST` | `/AppStore/znvr/v1/license/list` | `getLicenseList` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/license/add` | `addLicense` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/pool/migrate/create` | `createMigrateTask` | `form-urlencoded` | `new_pool`, `old_pool` |
| `POST` | `/AppStore/znvr/v1/record/pool/migrate/status` | `getMigrateTaskStatus` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/record/download/log` | `downloadFileLog` | `form-urlencoded` | `cam_uuid`, `path` |
| `POST` | `/AppStore/znvr/v1/permissions/list` | `getPermissionsList` | `form-urlencoded` | `cam_uuid` |
| `POST` | `/AppStore/znvr/v1/permissions/update` | `updatePermissions` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/info/play/snapshot` | `getRecordFileSnapshot` | `form-urlencoded` | `cam_uuid`, `path`, `sec` |
| `POST` | `/AppStore/znvr/v1/camera/info/timestamp2file` | `getFileInfoByTimestamp` | `form-urlencoded` | `cam_uuid`, `start` |
| `POST` | `/AppStore/znvr/v1/camera/snapshot/timeline` | `getSnapshotList` | `form-urlencoded` | `cam_uuid`, `end`, `start` |
| `POST` | `/AppStore/znvr/v1/conf/checkupdate` | `checkUpdate` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/add/check` | `cameraAddCheck` | `form-urlencoded` | `cam_from`, `cam_ip`, `cam_mac`, `cam_model`, `cam_name`, `cam_pool`, `cam_port`, `cam_rtsp`, `cam_type`, `cam_uuid`, `result`, `states` |
| `POST` | `/AppStore/znvr/v1/camera/db/config/list` | `dbConfigGet` | `form-urlencoded` | - |
| `POST` | `/AppStore/znvr/v1/camera/db/config/update` | `dbConfigSet` | `form-urlencoded` | `pool` |
| `POST` | `/zonedrive/auth/qiye` | `authQiye` | `form-urlencoded` | `nid`, `t`, `uuid` |
| `POST` | `/zonedrive/auth/check` | `check` | `form-urlencoded` | `rd` |
| `POST` | `/zonedrive/file/filelist` | `getList` | `form-urlencoded` | `cid`, `classification_id`, `limit`, `page`, `parent_file_id`, `path`, `show_dir`, `sort`, `start`, `system`, `type` |
| `POST` | `/zonedrive/file/file` | `getFileInfo` | `form-urlencoded` | - |
| `POST` | `/zonedrive/me/userinfo` | `getUserInfo` | `form-urlencoded` | - |
| `POST` | `/zonedrive/me/userdisk` | `getOneDriveRemain` | `form-urlencoded` | - |
| `POST` | `/zonedrive/task/download` | `addTask` | `form-urlencoded` | `dfiles`, `file_ids`, `save_path`, `srv_addr`, `store_dir` |
| `POST` | `/zonedrive/auth/logout` | `logout` | `form-urlencoded` | `srv_addr` |
| `POST` | `/zonedrive/task/list` | `getTasks` | `form-urlencoded` | `limit`, `page`, `state` |
| `POST` | `/zonedrive/task/action` | `changeTasks` | `form-urlencoded` | `task_id` |
| `POST` | `/zonedrive/sync/open` | `openAuto` | `form-urlencoded` | - |
| `POST` | `/zonedrive/sync/close` | `closeAuto` | `form-urlencoded` | - |
| `POST` | `/zonedrive/sync/delete` | `deleteAutoTask` | `form-urlencoded` | - |
| `POST` | `/zonedrive/sync/home` | `checkAutoStatus` | `form-urlencoded` | - |
| `POST` | `/zonedrive/sync/list` | `autoDirList` | `form-urlencoded` | - |
| `POST` | `/zonedrive/sync/add` | `addAutoList` | `form-urlencoded` | `file_ids`, `folder_ids`, `save_path` |
| `POST` | `/zonedrive/fail/list` | `fileFailList` | `form-urlencoded` | `limit`, `page`, `task_id` |
| `POST` | `/zonedrive/task/upload` | `upload` | `form-urlencoded` | `drive_id`, `drive_type`, `folder_id`, `paths`, `remote_dir`, `upload_paths` |
| `POST` | `/zonedrive/autobackup/info` | `getAutoBackupInfo` | `form-urlencoded` | - |
| `POST` | `/zonedrive/autobackup/add` | `autoUploadTaskAdd` | `form-urlencoded` | - |
| `POST` | `/zonedrive/autobackup/delete` | `autoUploadTaskDel` | `form-urlencoded` | - |
| `POST` | `/zonedrive/autobackup/start` | `autoUploadTaskStart` | `form-urlencoded` | - |
| `POST` | `/zonedrive/autobackup/stop` | `autoUploadTaskPause` | `form-urlencoded` | - |
| `POST` | `/zonedrive/autobackup/faillist` | `autoUploadFailList` | `form-urlencoded` | `limit`, `page` |
| `POST` | `/zonedrive/autobackup/clear_fail_files` | `clearFailFiles` | `form-urlencoded` | `file_ids` |
