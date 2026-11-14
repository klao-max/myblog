# 全平台 Bug 汇总报告

执行环境：本地 Python 3.12 服务，`APP_ENV=development`，Orca 内置 Chromium 浏览器，测试数据目录 `/private/tmp/signal-desk-e2e`。

## BUG-001 文章新建保存导致服务端异常

- **优先级**：P1
- **需求/用例**：TC-08
- **复现步骤**：管理员登录 → `/admin/posts/new` → 填写标题、摘要、正文、标签 → 发布状态选择“已发布” → 点击保存。
- **实际结果**：浏览器导航到 `/admin/posts/save` 并显示 `ERR_EMPTY_RESPONSE`；服务端日志抛出 `sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 9, and there are 10 supplied.`；文章未保存。
- **预期结果**：保存成功并返回文章管理列表，文章出现在公开首页和文章列表。
- **根因线索**：`app.py` 文章 INSERT 的参数包含 `cover` 字段，但 SQL 列表未包含 `cover`，占位符数量与绑定参数不一致。

## BUG-002 超大请求的 413 响应导致连接异常

- **优先级**：P1
- **需求/用例**：TC-25
- **复现步骤**：向 `/admin/login` POST 发送超过 256KB 的表单体。
- **实际结果**：服务端进入 `send_error(413, "请求内容过大")`，随后 `http.server` 在编码中文 reason phrase 时抛出 `UnicodeEncodeError: 'latin-1' codec can't encode characters`；客户端 `fetch` 收到 `Failed to fetch`，没有稳定的 413 响应。
- **预期结果**：客户端收到可解析的 413 或等价“请求内容过大”响应，服务继续处理后续请求。
- **根因线索**：`BaseHTTPRequestHandler.send_error` 的 HTTP reason phrase 只能使用 Latin-1，直接传入中文字符串会触发编码异常。
