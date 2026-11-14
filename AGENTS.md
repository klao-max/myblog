# Signal Desk 项目约定

这份文件是本仓库中所有代码代理和贡献者的默认工作约定。先读取与任务相关的 `README.md`、`PRODUCT.md`、`DESIGN.md` 和 `requirements.md`；如果任务涉及留言、图库、部署或安全，再读取对应的实现代码和测试，不凭空重建已有行为。

## 项目定位

Signal Desk 是一个个人内容平台：公开侧包含文章、项目、浏览器游戏和关于页；后台是单管理员内容工作台。公开页面优先保持安静、编辑化、可阅读，后台优先保持密度、状态清晰、操作直接。

## 技术栈与边界

- Python 3.12，标准库 HTTP 服务；主业务入口是根目录 `app.py`。
- SQLite 持久化；数据库由 `DATA_DIR` / `DATABASE_PATH` 决定，Docker 默认使用 `/data`。
- 公开页面和后台页面使用服务端 HTML 字符串渲染，不引入模板框架。
- 浏览器行为使用原生 `static/app.js`；视觉和响应式规则使用 `static/style.css`。
- Docker 使用 `Dockerfile` 和 `compose.yaml`；公网部署由 Caddy 反向代理提供 HTTPS。
- 不新增第三方运行时依赖，除非需求明确要求并同步更新 `Dockerfile`、依赖文档和测试。

## 开始工作前

1. 检查 `git status` 和最近提交，区分本次改动与工作区已有改动。
2. 先定位现有路由、数据表、渲染函数和测试，再决定扩展点；优先复用现有会话、CSRF、`esc`、`fmt_date`、`markdown`、`render_page` 和 `serve_file`。
3. 新功能先写一条会失败的行为测试，运行确认失败，再写最小实现；修复回归必须先写复现测试。
4. 修改公开 UI 前，保留现有交互语义和数据顺序；修改后台 UI 前，确认登录和 CSRF 流程仍然完整。

## 后端与数据约定

- 路由集中在 `Handler.do_GET` / `Handler.do_POST`；新增页面先定义独立渲染函数，再接入路由。
- 数据库表追加到 `SCHEMA`，使用幂等的 `CREATE TABLE IF NOT EXISTS`；不要依赖手工迁移才能启动。
- 使用参数化 SQL；不要拼接用户输入到 SQL、路径或 HTML。
- 所有用户可见文本通过 `esc()` 输出；留言、标题、摘要、回复等纯文本不解析为 Markdown 或 HTML。
- 公开写接口必须验证资源状态、字段长度、空值和请求频率；后台写接口必须验证管理员会话和 CSRF。
- IP 只从服务端连接地址读取；不把访客提交的 `X-Forwarded-For` 当作可信 IP。
- 上传文件必须限制类型、大小、数量和保存目录；存储文件名由服务端生成，公开文件服务必须做路径 containment 检查。
- 新增后台资源时，同时补齐：导航入口、列表/表单、保存/删除路由、空状态、移动端布局和对应测试。

## UI 视觉与交互

公开页面沿用当前“纸张编辑感”系统：

- 主色为纸张米色、深墨色、橙色强调和少量青绿色；不要随意引入紫色、霓虹渐变或另一套品牌色。
- 标题可使用现有衬线字体组合，正文和工具信息使用现有无衬线字体；层级靠字号、留白和规则线表达。
- 公开卡片和时间线保持硬边、低圆角、轻微偏移阴影；装饰必须服务内容顺序或状态。
- 后台使用深色工作台、细边框、青绿色状态色和橙色动作色；表单要有清晰标签、焦点态、错误态和明确的保存/删除动作。
- 组件必须支持 820px、560px、390px 宽度；横向内容只有在确有时间线/轨道语义时允许滚动，并提供可拖动滚动条。
- 所有按钮、链接、表单控件有键盘焦点；动画尊重 `prefers-reduced-motion`；图片有合适的 `alt` 或明确标记为装饰。
- 新 UI 优先扩展现有类和变量；CSS 新规则放在有注释的功能区，避免覆盖全局标签造成跨页面回归。

## JavaScript 约定

- 使用原生 DOM API，不引入框架或构建步骤。
- 只在目标组件存在时初始化；没有目标节点时安全退出。
- 定时器、悬停暂停、焦点暂停和手动暂停必须能互相同步；减少动画时不启动自动计时器。
- 脚本改动后运行 `node --check static/app.js`。

## 测试与验证

- Python 测试：`python3 -m unittest discover -s tests -v`。
- 语法检查：`python3 -m py_compile app.py tests/test_app.py`。
- CSS/HTML 改动至少检查相关渲染测试和 `git diff --check`；JS 改动额外运行 `node --check static/app.js`。
- 涉及 Docker 或公开路由时，使用 `docker compose -f compose.yaml up --build -d` 后检查 `docker compose ps`、健康状态和实际 HTTP 响应。
- 声称“完成”“修复”“通过”前必须提供本轮命令输出；如果浏览器或回环连接受环境限制，要明确说明未验证的部分。

## 提交风格

- 提交信息使用简短的 Conventional Commit 前缀：`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `style:`, `chore:`。
- 一次提交只表达一个完整意图；不要把无关格式化、`.DS_Store`、测试报告或临时文件带入提交。
- 提交前检查 `git diff --cached --check`、测试结果和 staged 文件列表。
- 不自动 push、合并或删除分支；这些动作需要明确指令。

## 部署与数据

- 本地直接运行默认端口 `8000`；Docker 应用端口为 `8001`，公网通过 Caddy 暴露 HTTPS。
- 必须通过 `ADMIN_PASSWORD` 和 `SESSION_SECRET` 启动；生产环境禁止使用默认密钥。
- `myblog_data` 保存 SQLite 和上传文件，`caddy_data` / `caddy_config` 保存证书与 Caddy 状态。
- 验证部署时保留 volume；除非明确要清空数据，不使用 `docker compose down -v`。
