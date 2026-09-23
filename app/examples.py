"""内置示例规则集，供前端一键载入。"""

EXAMPLES = [
    {
        "id": "basic",
        "name": "基础：旧路径 301 与 /api 内部改写",
        "rules": """# 旧版页面永久搬迁
REDIRECT ^/old/about$ /about 301

# /api/v1/xxx 在服务端内部映射到 /api/legacy/xxx，URL 不变
# （REWRITE 替换整条路径，用 $1 带上后缀）
REWRITE ^/api/v1/(.*)$ /api/legacy/$1 next

# 其余 /app/ 下的请求映射到入口文件
REWRITE ^/app/(.*)$ /index.php/$1 next
""",
        "path": "/old/about",
        "description": "演示最常见的 301 永久重定向与内部改写（next 链式继续）。",
    },
    {
        "id": "chain",
        "name": "链式：捕获组传递与 last 重启",
        "rules": """# 去掉 .html 后缀，替换后继续下一条
REWRITE ^/docs/(.+)\\.html$ /docs/$1 next

# 把 docs 改名为 help，并从头重新匹配（last）
REWRITE ^/docs/(.+)$ /help/$1 last

# 最终映射到帮助中心入口
REWRITE ^/help/(.+)$ /app/help.py?page=$1 next
""",
        "path": "/docs/guide/install.html",
        "description": "替换串中的 $1 引用捕获组；last 会让规则链从第 1 条重新执行。",
    },
    {
        "id": "loop",
        "name": "循环检测：last 规则互相改写",
        "rules": """# 两条规则都用 last（从头重新匹配），于是无限互相改写
REWRITE ^/a/(.*)$ /b/$1 last
REWRITE ^/b/(.*)$ /a/$1 last
""",
        "path": "/a/x",
        "description": "last 让规则链从头重跑，路径在 /a/ 与 /b/ 之间反复横跳，引擎检测到同一规则对同一路径二次命中后返回 508。",
    },
    {
        "id": "realworld",
        "name": "实战：去后缀、补斜杠、302 活动页",
        "rules": """# 活动入口临时跳转
REDIRECT ^/promo$ /promo/spring-2026 302

# 无扩展名的目录路径统一补上结尾斜杠
REWRITE ^(/[a-z]+)$ $1/ next

# 隐藏 .php 后缀：/about.php 内部映射到 /about
REWRITE ^/(.+)\\.php$ /$1 next
""",
        "path": "/promo",
        "description": "多条规则顺序协作，包含 302 临时跳转与常见的 URL 美化手法。",
    },
    {
        "id": "named",
        "name": "命名捕获组：多语言路由",
        "rules": """# /zh/guide/intro -> /index.php?lang=zh&page=guide/intro
REWRITE ^/(?P<lang>[a-z]{2})/(?P<page>.+)$ /index.php?lang=${lang}&page=${page} next
""",
        "path": "/zh/guide/intro",
        "description": "使用 (?P<名字>...) 命名捕获组，并通过 ${名字} 在替换串中引用。",
    },
    {
        "id": "errors",
        "name": "语法错误定位示例（故意写错）",
        "rules": """# ① 括号未闭合
REWRITE ^/users/([0-9]+ /api/users?id=$1 next
# ② 引用了不存在的第 2 个捕获组
REWRITE ^/post/([0-9]+)$ /article?id=$2 next
# ③ 未知执行标志
REWRITE ^/x/ /y/ continue
# ④ 重定向状态码不合法
REDIRECT ^/home$ / 299
# ⑤ 未知动作
REWITE ^/a/ /b/ next
# ⑥ 引用不存在的命名组
REWRITE ^/(?P<slug>[a-z]+)$ /p?s=${name} next
""",
        "path": "/users/42",
        "description": "包含 6 类常见语法错误，点击错误信息可跳转到对应行列。",
    },
]
