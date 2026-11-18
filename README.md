# Signal Desk / 个人博客平台

一个包含博客、项目和 HTML 游戏的个人内容平台。第一版包含单管理员网页后台、SQLite 持久化和 Docker Desktop 部署配置。

## 本地直接运行

需要 Python 3.12+：

```powershell
cd D:\Users\MyProject\myblog
$env:APP_ENV="development"
$env:ADMIN_PASSWORD="set-a-local-password"
$env:SESSION_SECRET="set-a-local-session-secret"
python app.py
```

打开 <http://127.0.0.1:8000>。

后台：<http://127.0.0.1:8000/admin/login>

用户名默认为 `admin`，密码和 session secret 取环境变量 `ADMIN_PASSWORD`、`SESSION_SECRET`。生产环境未设置这两个变量时会拒绝启动。

## 腾讯云生产部署（Caddy + HTTPS）

Compose 已包含博客应用和 Caddy 反向代理，生产域名固定为：

<https://blog.kangmouren.online>

先在腾讯云 DNS 中添加一条记录：

```text
类型：A
主机记录：blog
记录值：你的腾讯云服务器公网 IPv4
```

服务器安全组放行 `80/TCP`、`443/TCP`；如果要启用 Caddy 的 HTTP/3，再放行 `443/UDP`，并限制 `22` 只允许你的管理 IP。将项目上传到服务器后，在项目目录执行：

```bash
printf 'APP_ENV=production\nADMIN_USERNAME=admin\nADMIN_PASSWORD=%s\nSESSION_SECRET=%s\n' \\
  "$(openssl rand -hex 24)" \\
  "$(openssl rand -hex 32)" > .env
chmod 600 .env

docker compose up -d --build
docker compose ps
docker compose logs -f caddy
```

Caddy 会在 DNS 生效且 80/443 可访问后自动申请和续期 HTTPS 证书。部署完成后访问 <https://blog.kangmouren.online>，后台地址为 <https://blog.kangmouren.online/admin/login>。

应用容器只绑定本机 `127.0.0.1:8001`，公网流量统一经过 Caddy。Caddy 的证书和配置保存在 `caddy_data`、`caddy_config` 两个 volume 中。

停止或重启服务：

```bash
docker compose down
docker compose up -d
```

不要使用 `docker compose down -v`，否则会删除数据库、上传文件和 Caddy 证书。

## 使用 Docker Desktop

确保 Docker Desktop 已启动，并先设置 `ADMIN_PASSWORD`、`SESSION_SECRET`，然后在 PowerShell 执行：

```powershell
cd D:\Users\MyProject\myblog
docker compose -f compose.yaml up --build -d
```

本地直接检查应用可打开 <http://127.0.0.1:8001>；生产后台登录请使用上面的 HTTPS 域名。

查看日志：

```powershell
docker compose -f compose.yaml logs -f myblog
```

停止服务：

```powershell
docker compose -f compose.yaml down
```

SQLite 和上传文件都存在 Docker volume `myblog_data` 中，重启容器不会丢失文章、项目、游戏元数据或图库图片。

应用启动时会自动执行兼容性数据库迁移并启用 SQLite WAL。迁移失败会阻止应用进入可用状态，部署前仍应备份 `myblog_data`。Compose readiness 检查数据库查询和 `/data` 写入能力。

图库文件也会持久化在同一个 volume：数据库位于 `/data/blog.db`，上传的背景图片位于 `/data/uploads/gallery/`。停止或重新创建容器时保留 `myblog_data` 即可保留图片；不要使用 `docker compose down -v`，因为该命令会删除 volume 及其中的数据库和上传文件。部署前请按自己的 Docker 运维流程备份 `myblog_data`。

## 管理首页背景图库

登录后台后打开 <http://127.0.0.1:8001/admin/media>（直接运行 `python app.py` 时使用端口 8000）。在“图库”页面可以：

- 一次选择并上传多张图片；支持 JPEG、PNG、WebP 和 GIF，每张图片最大 8 MB，最多保留 9 张
- 使用“启用”开关控制图片是否出现在博客背景轮播中，并用上下箭头调整顺序
- 选择“淡入淡出”“滑动”或“缩放”效果；设置会保存到 SQLite，刷新页面或重启容器后仍然生效
- 删除图片；删除最后一张后，博客页会自动回到没有背景图库的默认状态

上传后打开 `/blog` 检查公开效果。图片文件由应用保存到 `DATA_DIR/uploads/gallery`；Docker 部署时 `DATA_DIR=/data`，因此上传内容随 `myblog_data` volume 一起持久化。

## 文章留言

已发布文章底部提供公开留言区，访客无需登录即可提交建议。留言提交后立即显示；管理员可在 <http://127.0.0.1:8001/admin/comments>（直接运行时使用 8000 端口）查看文章、留言时间和 IP，并编辑留言、填写回复、隐藏/恢复显示或删除。

留言只保存纯文本，单条最多 2000 字；公开页面不会显示 IP。系统按 IP 做短时间提交频率限制，并使用隐藏字段拦截常见机器人提交。

## 添加游戏

将每个游戏放到：

```text
games/<slug>/index.html
```

然后在后台添加游戏记录，`slug` 必须和目录名一致。公开运行地址会是：

```text
/games/<slug>/play
```

## 第一版范围

- 文章：草稿、发布、标签、简单 Markdown、封面地址
- 项目：技术栈、GitHub 地址、Demo 地址、项目状态、首页精选
- 游戏：说明、操作方式、源码链接、静态 HTML 游戏、首页精选
- 后台：单管理员登录、概览、三类内容的增改和发布状态

## 生产环境前需要替换

- 将 `ADMIN_PASSWORD`、`SESSION_SECRET` 放到服务器本机 `.env` 或部署平台的 secret 中
- 配置 `blog.kangmouren.online` 的 DNS A 记录
- 按运维流程备份 `myblog_data`、`caddy_data` 和 `caddy_config`

