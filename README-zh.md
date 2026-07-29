# Eh Downloader

Eh Downloader 是一个下载 E-Hentai / ExHentai 画廊 ZIP 的 Web 应用，支持 Original Archive 和 Resample Archive。

通过官方归档页面下载实现，不通过逐页解析图片地址，也不依赖 H@H Downloader，全由 EH 官方归档页面自行处理你账号的免费额度、GP 费用以及必要的 Credits 兑换。

## 主要功能

![截图](https://github.com/yeraph-plus/eh-downloader/blob/main/screenshots/110647.jpg)

![截图](https://github.com/yeraph-plus/eh-downloader/blob/main/screenshots/110815.jpg)

- Submit gallery URLs directly, one URL per line.
- 直接提交画廊链接 URL ，每行一个。
- 下载原始归档或重采样归档。
- 管理多个 EH 账户，并记录 GP、Credits 和账户状态。
- 可选本地缓存、容量限制和定时清理，并支持全中转下载。
- 单管理员登录、Bearer Token 令牌、可开放的游客权限。

## 镜像

此项目由 GitHub Actions 预构建两个镜像发布到 GHCR package：

```text
ghcr.io/yeraph-plus/eh-downloader:latest  # Eh Downloader Web/Worker
ghcr.io/yeraph-plus/eh-downloader:hath    # 一个单独的 H@H Client 易部署包装
```

## 快速部署

要求：

- Docker Engine 或 Docker Desktop
- Docker Compose v2

克隆项目并准备配置：

```bash
git clone https://github.com/yeraph-plus/eh-downloader.git
cd eh-downloader
cp .env.example .env
mkdir -p data cache
openssl rand -hex 32
```

编辑 `.env`，至少修改以下配置：

```dotenv
APP_SECRET=上一步生成的随机字符串
ADMIN_PASSWORD=一个足够长的管理密码
EH_DOWNLOADER_IMAGE=ghcr.io/yeraph-plus/eh-downloader:latest
```

`APP_SECRET` 用于加密账户 Cookie。部署后不要随意更换，否则数据库中已有的账户凭据将无法解密。

启动服务：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

默认访问地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。就绪检查地址为 `/health/ready`，API 文档位于 `/docs`。

## 常用配置

`.env` 中只需设置以下变量。其他配置均有合理默认值，启动后可在设置页调整。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `APP_SECRET` | 是 | 随机字符串（最少 32 位），用于加密账户 Cookie |
| `ADMIN_PASSWORD` | 是 | 管理员登录密码（最少 10 位） |
| `EH_DOWNLOADER_IMAGE` | 否 | 容器镜像标签 |
| `WEB_BIND_ADDRESS` | 否 | Web 监听地址（默认 `127.0.0.1`） |
| `WEB_PORT` | 否 | Web 映射端口（默认 `8000`） |
| `SECURE_COOKIES` | 否 | HTTPS 部署后应设为 `true` |
| `EH_PROXY_URL` | 否 | EH 请求使用的 HTTP/SOCKS 代理地址 |

管理员用户名默认为 `admin`。缓存、游客权限、下载大小限制和 Worker 并发均可在启动后的设置页调整。

Tips：Docker 使用宿主机代理时，不能在容器内填写 `127.0.0.1`。例如宿主机代理端口为 `7890`：

```dotenv
EH_PROXY_URL=http://host.docker.internal:7890
```

## 使用说明

### 添加账户

1. 使用 `.env` 中的管理员用户名和密码登录。
2. 打开设置页，选择添加账户。
3. 导入标准 Netscape 格式的 `cookies.txt`。
4. 返回主页并提交画廊任务。

Cookie 至少应包含 `ipb_member_id` 和 `ipb_pass_hash`，访问 ExHentai 通常还需要 `igneous`。可以使用浏览器扩展 “Get cookies.txt LOCALLY” 导出。Cookie 会加密保存在本地数据库中，界面和 API 不会回显原文。

只在实际处理下载任务时访问 EH 检查状态。认证失效的账户会自动停用，临时网络错误/GP耗尽等无法下载时会让账户进入冷却。

### 创建任务

在主页文本框中粘贴一个或多个画廊 URL，每行一个，然后选择归档类型：

```text
https://e-hentai.org/g/123456/abcdef0123/
https://exhentai.org/g/234567/0123abcdef/
```

一次最多提交 100 个链接。相同画廊的 Original 与 Resample 视为不同任务；重复提交同一类型不会创建重复记录。

任务由后台 Worker 自动处理。归档准备完成后，列表会显示文件名、大小、状态、错误信息和下载按钮。普通网络错误最多自动重试 3 次；余额、认证或页面协议错误会直接显示失败原因。

### 缓存与直接下载

- 容量限制：设置一个硬盘占用上限，已满时新任务等待已有缓存到期并被清理，不会主动删除仍在保留期内的文件。
- 开启缓存：下载并校验 ZIP 后保存到 `./cache`，后续相同画廊任务可直接恢复下载。
- 关闭缓存：只准备官方归档地址，下载时由本机中转，不在本地保留 ZIP。
- 文件过期或丢失：对应下载入口会失效，相关无效记录由清理流程处理。

### 游客访问

游客功能默认关闭。游客为公共身份，权限为：

- 允许游客查看归档列表。
- 禁止或允许游客创建 Resample/Original 任务。

## HTTPS 与安全

生产环境建议保持：

```dotenv
WEB_BIND_ADDRESS=127.0.0.1
SECURE_COOKIES=true
```

然后通过 Caddy、Nginx、Traefik 等反向代理提供 HTTPS ，此应用内并未提供包括 IP 限流等任何反滥用支持，公开部署时应在反向代理层增加访问频率限制。

## 更新与备份

更新镜像：

```bash
docker compose pull
docker compose up -d
```

需要备份：

- `.env`：包含解密账户 Cookie 所需的 `APP_SECRET`。
- `./data`：SQLite 数据库和应用设置。
- `./cache`：可选，丢失后只影响现有缓存文件。

备份数据库前建议先停止服务：

```bash
docker compose down
```

`docker compose down` 不会删除绑定目录中的数据。不要使用 `docker compose down -v` 清理仍需保留的 H@H 命名卷。

## 可选 H@H Client 的说明

H@H Client 只是一个独立、方便部署的附加容器，与 Eh Downloader 本体无关。

其服务和命名卷默认在 `compose.yaml` 中整段注释。需要启用时：

1. 取消 `hath` 服务部分的注释。
2. 取消 `.env` 中 H@H 配置的注释。
3. 设置 `HATH_IMAGE`、`HATH_CLIENT_ID`、`HATH_CLIENT_KEY` 和 `HATH_PORT`。
4. 启动 H@H 服务。

```bash
docker compose pull hath
docker compose up -d hath
```

此 H@H 容器使用 host 网络直接监听 `HATH_PORT`。该端口必须与 EH 控制面板配置一致，并在 VPS 防火墙中放行。

## 本地开发

后端测试：

```bash
python -m pip install "./backend[test]"
python -m pytest backend/tests
```

前端构建：

```bash
cd frontend
npm install
npm run build
```

本地构建应用镜像：

```bash
docker build -f docker/web.Dockerfile -t eh-downloader:local .
```
