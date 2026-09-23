# 🔁 URL 重写规则调试台

基于 **FastAPI + 原生 JavaScript** 的 URL 重写规则调试台。用户在左侧定义按顺序执行的
路径匹配、捕获组替换、重定向或内部改写规则，并输入一条请求路径；服务端逐步模拟整条
规则链，展示**每一次匹配、捕获组变量变化、响应状态与最终 URL**，并自动检测循环、
在超过步数上限时停止。支持规则语法错误精确定位、内置示例一键载入、调试结果复制。

---

## ✨ 功能特性

- **顺序规则链**：自上而下逐行测试；`REWRITE` 改写后可继续下一条（`next`）或从头重启（`last`），`REDIRECT` 命中即终止。
- **逐步执行轨迹**：每条规则的命中 / 未命中、规则原文、行号、捕获组（`$0` 整匹配、数字组、命名组）、路径前后变化、流向标签。
- **最终结果**：HTTP 状态码（200 / 301 / 302 / 303 / 307 / 308 / 508 循环 / 509 超步数）、最终 URL、`Location` 响应头、执行步数 / 重定向跳数。
- **循环检测**：以 `(路径, 规则位置, 重定向跳数)` 为状态指纹，发现重复立即返回 **508 Loop Detected**，不会死循环。
- **步数上限**：默认 100 步（可调 1–500），超限返回 **509**；跟随重定向时最多 20 跳。
- **语法错误定位**：括号未闭合、组引用不存在、非法状态码、未知动作等错误给出**行号 + 列号**，点击错误即可跳转选中。
- **内置示例**：基础重定向、链式改写、循环检测、实战规则集、命名捕获组、语法错误示例，一键载入。
- **复制调试结果**：一键复制包含请求、最终状态、逐步轨迹与捕获组的纯文本报告。
- **场景分享链接**：一键生成包含规则文本、请求路径、步数上限与跟随重定向选项的链接；对方打开即恢复相同编辑状态。状态经**带版本号的 UTF-8 Base64URL** 编码后放在 URL 片段（`#v1=…`）中，**不会发送到服务端**；链接损坏、版本未知或字段非法时显示可见提示并保留默认界面。
- **单命令启动**：Docker Compose 或本地 uvicorn，前端为零构建原生 JS。

---

## 🚀 快速开始

### 方式一：Docker Compose（推荐，单命令）

```bash
docker compose up --build
```

启动后用浏览器打开：

> **http://localhost:8000**

端口映射为宿主机 **8000** → 容器 8000。停止用 `Ctrl+C`，后台运行用
`docker compose up -d --build`，停止并移除用 `docker compose down`。

### 方式二：本地 Python 运行

需要 Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

同样访问 http://localhost:8000 。

> 如需更换端口，修改 `docker-compose.yml` 中 `"宿主端口:8000"` 的左侧，
> 或调整 uvicorn 的 `--port` 参数即可。容器内服务固定监听 8000。

---

## 📖 规则语法

每行一条规则，以 `#` 开头（或行尾 ` #`）为注释，空行忽略。支持双引号包裹的参数
（用于包含空格或 `#` 的正则 / 替换串）。

### `REWRITE` —— 内部改写（URL 不变，继续走规则链）

```
REWRITE  <正则表达式>  <替换串>  [next|last]
```

| 参数 | 说明 |
| --- | --- |
| 正则 | Python 风格正则，**非锚定搜索**（与 nginx 一致，子串命中即匹配）；建议用 `^` / `$` 锚定 |
| 替换串 | `$0` 整匹配、`$1`..`$n` 第 n 个捕获组、`${1}` 歧义消除、`${name}` 命名组、`$$` 字面量 `$` |
| 标志 | 可省略，默认 `next`：替换后继续下一条；`last`：替换后**从第 1 条规则重新匹配** |

> **整路径替换**：`REWRITE` 命中后以替换串作为**整条新路径**（与 nginx/rewrite
> 一致），正则未捕获的部分不会自动保留——需要时用 `$0` 或 `(.*)` + `$1` 带上，
> 例如 `REWRITE ^/api/v1/(.*)$ /api/legacy/$1 next`。

### `REDIRECT` —— 重定向（规则链终止，返回 3xx）

```
REDIRECT  <正则表达式>  <替换串>  <状态码>
```

状态码可选：`301`（永久）、`302`（默认式临时）、`303`（See Other）、
`307`（临时保留方法）、`308`（永久保留方法）。

替换结果以单个 `/` 开头时视为**站内地址**；勾选「跟随站内重定向」后，浏览器发起
新请求、规则链从第 1 条重新模拟（计入跳数）。以 `http://`、`https://`、`//`
开头的站外地址不继续模拟。

### 执行模型

1. 规则从第 1 条开始，用正则依次测试当前路径；
2. 未命中 → 尝试下一条；全部未命中 → **200 内部放行**，当前路径即最终 URL；
3. `REWRITE` 命中 → 按捕获组替换路径，按 `next` / `last` 推进；
4. `REDIRECT` 命中 → 立即返回对应 3xx 与 `Location`；
5. 同一 `(路径, 规则下标, 跳数)` 状态再次出现 → **508 循环**；
6. 执行步数达到上限 → **509 超限停止**。

### 示例

```nginx
# /old/about 永久搬迁到 /about
REDIRECT ^/old/about$ /about 301

# 去掉 .html 后缀后继续下一条
REWRITE ^/docs/(.+)\.html$ /docs/$1 next

# docs 改名 help，并从头重新匹配
REWRITE ^/docs/(.+)$ /help/$1 last

# 命名捕获组：/zh/guide -> /index.php?lang=zh&page=guide
REWRITE ^/(?P<lang>[a-z]{2})/(?P<page>.+)$ /index.php?lang=${lang}&page=${page} next
```

---

## 🔗 场景分享链接

点击「🔗 复制分享链接」，当前编辑状态（**规则文本、请求路径、步数上限、跟随重定向
选项**）会被编码进链接并复制到剪贴板，同时同步到浏览器地址栏。任何人打开该链接，
四项内容都会原样恢复到编辑区（不会自动运行，点「运行调试」即可查看结果）。

链接格式：

```
http://localhost:8000/#v1=<Base64URL(JSON)>
```

- **仅存在于 URL 片段（`#` 之后）**：浏览器不会把片段发给服务端，规则文本不会出现在
  服务端访问日志里；`v1` 是格式版本号，JSON 以 **UTF-8** 序列化后再做 Base64URL
  （`-` / `_`，无填充），可安全放入链接、完整保留中文等多字节字符。
- **打开时严格校验**：载荷损坏（截断 / 非法 Base64URL / 非法 UTF-8 / 非法 JSON）、
  版本号未知、出现非约定字段或字段类型 / 取值非法（如步数越界、路径不以 `/` 开头）时，
  页面上方显示**可关闭的醒目提示**，编辑区保持默认界面不被污染，同时自动清除损坏片段。
- 片段并非分享链接（如普通页内锚点）时静默忽略；规则文本很长时链接也会很长，建议通过
  剪贴板直接粘贴发送，避免手工删改片段内容。

状态 JSON 的四个字段：

```json
{ "v": 1, "rules": "REWRITE ^/a$ /b next", "path": "/a",
  "max_steps": 100, "follow_redirects": false }
```

---

## 🔌 HTTP API

### `POST /api/debug`

```json
{
  "rules": "REWRITE ^/a/ /b/ next",
  "path": "/a/x",
  "max_steps": 100,
  "follow_redirects": false
}
```

成功响应（关键字段）：

```json
{
  "ok": true,
  "rule_count": 1,
  "trace": [
    { "seq": 1, "type": "rewrite", "line": 1, "rule": "REWRITE ^/a/ /b/ next",
      "matched": true, "path": "/a/x", "new_path": "/b/x",
      "groups": [{ "index": 0, "name": null, "value": "/a/" }],
      "flow": "next：继续下一条", "status": null, "location": null }
  ],
  "result": {
    "final_url": "/b/x",
    "status": 200,
    "location": null,
    "finish_reason": "fallthrough",
    "steps": 1,
    "hops": 0
  }
}
```

语法错误响应（`errors` 中含 `line` / `col` / `length` / `message`）：

```json
{ "ok": false, "rule_count": 0, "errors": [
  { "line": 1, "col": 18, "length": 1, "message": "正则表达式错误：missing ), unterminated subpattern", "source": "..." }
] }
```

其余端点：`GET /api/examples`（内置示例）、`GET /healthz`（健康检查）、
`GET /`（调试台页面）、交互式文档 `GET /docs`。

---

## 🗂 项目结构

```
.
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
└── app
    ├── __init__.py
    ├── main.py          # FastAPI 入口（/api/debug、静态页面）
    ├── engine.py        # 词法/语法解析、错误定位、替换串展开
    ├── simulator.py     # 规则链逐步模拟、循环检测、步数上限
    ├── examples.py      # 内置示例
    └── static
        ├── index.html   # 调试台页面
        ├── style.css
        ├── share.js     # 场景分享编解码（带版本号 UTF-8 Base64URL，纯函数可单测）
        └── app.js       # 原生 JS（编辑器、轨迹渲染、复制报告、分享链接恢复）
```

## ⚠️ 说明

- 本工具用于**规则逻辑的教学与调试**，正则在服务端直接编译执行，请勿在不可信环境暴露给公网用于执行他人提交的复杂正则（ReDoS 风险）；步数上限只约束规则执行次数，不限制单条正则自身的回溯开销。
- 调试台只模拟路径（含查询串）的变换，不涉及真实后端转发与域名拼接。
