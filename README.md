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

## 使用 Docker Desktop

确保 Docker Desktop 已启动，并先设置 `ADMIN_PASSWORD`、`SESSION_SECRET`，然后在 PowerShell 执行：

```powershell
cd D:\Users\MyProject\myblog
docker compose -f compose.yaml up --build -d
```

打开 <http://127.0.0.1:8001>。

查看日志：

```powershell
docker compose -f compose.yaml logs -f myblog
```

停止服务：

```powershell
docker compose -f compose.yaml down
```

SQLite 存在 Docker volume `myblog_data` 中，重启容器不会丢失文章、项目和游戏元数据。

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

- 将 `ADMIN_PASSWORD`、`SESSION_SECRET` 放到本机 `.env` 或部署平台的 secret 中
- 为公网环境增加 HTTPS、反向代理和备份策略

