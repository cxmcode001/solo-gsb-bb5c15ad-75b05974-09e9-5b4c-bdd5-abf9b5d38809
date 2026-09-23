"""引擎与 API 的端到端冒烟测试（不依赖 pytest）。"""

import sys
import traceback

from fastapi.testclient import TestClient

from app.engine import parse_rules
from app.examples import EXAMPLES
from app.main import app
from app.simulator import simulate

client = TestClient(app)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# 1. 解析：正常规则
# ---------------------------------------------------------------------------

section("解析正常规则")
rules, errors = parse_rules(
    """\
# comment
REWRITE ^/a/ /b/ next
REWRITE ^/c/ /d/
REDIRECT ^/old$ /new 301
REWRITE "^/x y/" "/to z/" last
"""
)
check("无错误", not errors, [e.message for e in errors])
check("4 条规则", len(rules) == 4)
check("默认标志为 next", rules[1].flag == "next")
check("引号内空格保留", rules[3].pattern_src == "^/x y/")
check("引号替换串", rules[3].replacement == "/to z/", rules[3].replacement)
check("redirect 状态码", rules[2].status == 301)

# ---------------------------------------------------------------------------
# 2. 解析：错误定位
# ---------------------------------------------------------------------------

section("语法错误定位")
src = """\
REWRITE ^/users/([0-9]+ /api/u?id=$1 next
REWRITE ^/post/([0-9]+)$ /article?id=$2 next
REWRITE ^/x/ /y/ continue
REDIRECT ^/home$ / 299
REWITE ^/a/ /b/ next
REWRITE ^/(?P<slug>[a-z]+)$ /p?s=${name} next
REDIRECT ^/h$ /t 301 extra
"""
rules, errors = parse_rules(src)
msgs = {(e.line, e.message) for e in errors}
check("共 7 个错误", len(errors) == 7, [f"{e.line}:{e.col} {e.message}" for e in errors])
check("① 括号未闭合", any("正则" in e.message and e.line == 1 for e in errors))
check("② $2 不存在", any(e.line == 2 and "第 2" in e.message for e in errors))
check("③ 未知标志", any(e.line == 3 and "标志" in e.message for e in errors))
check("④ 非法状态码", any(e.line == 4 and "301/302" not in e.message and "299" in e.message for e in errors))
check("⑤ 未知动作", any(e.line == 5 and "REWRITE" in e.message for e in errors))
check("⑥ 命名组不存在", any(e.line == 6 and "name" in e.message for e in errors))
check("⑦ 多余参数", any(e.line == 7 and "多余" in e.message for e in errors))
check("错误均带行列", all(e.line >= 1 and e.col >= 1 and e.length >= 1 for e in errors))

# 引号与转义错误
_, errors2 = parse_rules('REWRITE ^/a/ "/unclosed next')
check("未闭合引号", len(errors2) == 1 and "引号" in errors2[0].message)
_, errors3 = parse_rules("REWRITE ^/a/ /x?q=$9 next")
check("$9 超出组数", errors3 and "第 9" in errors3[0].message)

# 合法：$0、${1}、命名组
rules4, errors4 = parse_rules(
    r'REWRITE ^/(?P<lang>[a-z]{2})/(?P<page>.+)$ /i?l=${lang}&p=${page}&all=$0 next'
)
check("命名组/花括号/$0 合法", not errors4, [e.message for e in errors4])

# ---------------------------------------------------------------------------
# 3. 模拟：基础 next 链 + fallthrough
# ---------------------------------------------------------------------------

section("模拟基础链路")
rules, _ = parse_rules(
    "REWRITE ^/api/v1/(.*)$ /api/legacy/$1 next\n"
    "REWRITE ^/app/(.*)$ /index.php/$1 next\n"
)
res = simulate(rules, "/api/v1/users")
check("最终路径", res["result"]["final_url"] == "/api/legacy/users", res["result"])
check("状态 200", res["result"]["status"] == 200)
check("2 步命中 + 结束", res["result"]["steps"] == 2, res["result"])
types = [t["type"] for t in res["trace"]]
check("轨迹含 start/test/rewrite/end",
      types == ["start", "rewrite", "test", "end"], types)
check("未命中步骤标记", res["trace"][2]["matched"] is False)
check("捕获组 $0", res["trace"][1]["groups"][0]["value"] == "/api/v1/users")

# ---------------------------------------------------------------------------
# 4. last 重启 + 捕获组传递
# ---------------------------------------------------------------------------

section("last 重启与捕获组")
rules, _ = parse_rules(
    r"""
REWRITE ^/docs/(.+)\.html$ /docs/$1 next
REWRITE ^/docs/(.+)$ /help/$1 last
REWRITE ^/help/(.+)$ /app/help.py?page=$1 next
"""
)
res = simulate(rules, "/docs/guide/install.html")
r = res["result"]
check("多步替换最终 URL", r["final_url"] == "/app/help.py?page=guide/install", r)
check("last 重启后共 5 步（含重启后的未命中测试）", r["steps"] == 5, r)
g1 = res["trace"][1]["groups"]
check("$1 捕获 html 路径", g1[1]["value"] == "guide/install", g1)

# ---------------------------------------------------------------------------
# 5. 重定向（不跟随）
# ---------------------------------------------------------------------------

section("重定向")
rules, _ = parse_rules("REDIRECT ^/old/about$ /about 301")
res = simulate(rules, "/old/about")
check("301", res["result"]["status"] == 301)
check("Location", res["result"]["location"] == "/about")
check("finish redirect", res["result"]["finish_reason"] == "redirect")
check("1 步终止", res["result"]["steps"] == 1)

# 外部地址
rules, _ = parse_rules("REDIRECT ^/ext$ https://example.com/ 302")
res = simulate(rules, "/ext", follow_redirects=True)
check("站外地址不跟随", res["result"]["status"] == 302
      and res["result"]["hops"] == 0)

# ---------------------------------------------------------------------------
# 6. 跟随站内重定向
# ---------------------------------------------------------------------------

section("跟随重定向")
rules, _ = parse_rules(
    "REDIRECT ^/promo$ /promo/spring 302\n"
    "REWRITE ^/promo/spring$ /pages/promo next\n"
)
res = simulate(rules, "/promo", follow_redirects=True)
check("跟随到达内部路径", res["result"]["final_url"] == "/pages/promo", res["result"])
check("1 跳", res["result"]["hops"] == 1)
check("最终 200", res["result"]["status"] == 200)

# 重定向循环
rules, _ = parse_rules(
    "REDIRECT ^/a$ /b 302\nREDIRECT ^/b$ /a 302\n"
)
res = simulate(rules, "/a", follow_redirects=True)
check("重定向循环 508", res["result"]["status"] == 508, res["result"])
check("循环原因", res["result"]["finish_reason"] == "loop")

# ---------------------------------------------------------------------------
# 7. 改写循环 & 步数上限
# ---------------------------------------------------------------------------

section("循环检测与步数上限")
# 真正的循环需要 last 重启（next 只向前推进，会自然走到链尾放行）
rules, _ = parse_rules(
    "REWRITE ^/a/(.*)$ /b/$1 last\nREWRITE ^/b/(.*)$ /a/$1 last\n"
)
res = simulate(rules, "/a/x", max_steps=100)
check("改写循环 508", res["result"]["status"] == 508, res["result"])
check("轨迹末节点 loop", res["trace"][-1]["type"] == "loop")

# 固定点自循环：替换后路径不变 + last
rules, _ = parse_rules("REWRITE ^/x/(.*)$ /x/$1 last")
res = simulate(rules, "/x/y")
check("固定点自循环 508", res["result"]["status"] == 508, res["result"])

# 重定向到自身的循环（跟随模式）
rules, _ = parse_rules("REWRITE ^/a/(.*)$ /b/$1 next\nREWRITE ^/b/(.*)$ /a/$1 last")
res = simulate(rules, "/a/x", max_steps=1)
check("1 步上限 509", res["result"]["status"] == 509, res["result"])
check("finish step_limit", res["result"]["finish_reason"] == "step_limit")

# next 互改写不是循环：走到链尾放行
rules, _ = parse_rules(
    "REWRITE ^/a/(.*)$ /b/$1 next\nREWRITE ^/b/(.*)$ /a/$1 next\n"
)
res = simulate(rules, "/a/x")
check("next 互改写放行（非循环）", res["result"]["status"] == 200
      and res["result"]["final_url"] == "/a/x", res["result"])

# 空规则
res = simulate([], "/anything")
check("无规则放行 200", res["result"]["status"] == 200
      and res["result"]["final_url"] == "/anything")

# ---------------------------------------------------------------------------
# 8. API 端到端
# ---------------------------------------------------------------------------

section("HTTP API")

def post(body):
    return client.post("/api/debug", json=body)

r = post({"rules": "REWRITE ^/a/(.*)$ /b/$1 next", "path": "/a/x",
          "max_steps": 100, "follow_redirects": False})
check("POST 200", r.status_code == 200, r.text)
data = r.json()
check("ok=True", data["ok"] is True)
check("API 最终 URL", data["result"]["final_url"] == "/b/x")

r = post({"rules": "REWRITE ^/([0-9 /x next", "path": "/a"})
check("错误请求 200(语义错误)", r.status_code == 200)
check("ok=False 且含定位", r.json()["ok"] is False and r.json()["errors"][0]["col"] >= 1)

r = post({"rules": "", "path": "no-slash"})
check("路径缺斜杠 422", r.status_code == 422)

r = post({"rules": "", "path": "/x", "max_steps": 9999})
check("max_steps 越界 422", r.status_code == 422)

r = post({"rules": "", "path": "/x y"})
check("路径含空格 422", r.status_code == 422)

r = client.get("/api/examples")
check("示例接口", r.status_code == 200 and len(r.json()) == len(EXAMPLES))

r = client.get("/healthz")
check("健康检查", r.status_code == 200 and r.json()["status"] == "ok")

r = client.get("/")
check("首页", r.status_code == 200 and "重写规则调试台" in r.text)

r = client.get("/static/app.js")
check("静态 JS", r.status_code == 200)

r = client.get("/static/share.js")
check("分享编解码 JS 可访问", r.status_code == 200 and "ShareCodec" in r.text)

r = client.get("/")
html = r.text
check("首页引入 share.js", "/static/share.js" in html)
check("首页含复制分享链接按钮", 'id="shareBtn"' in html)
check("首页含分享错误提示容器", 'id="shareNotice"' in html)
# 片段链接只在客户端解析，服务端不提供任何分享状态接口
check("服务端无分享状态端点", "/api/share" not in [r.path for r in app.routes])

# ---------------------------------------------------------------------------
# 9. 全部内置示例可正常执行（errors 示例除外，其余必须 ok 且终止）
# ---------------------------------------------------------------------------

section("内置示例回归")
for ex in EXAMPLES:
    r = post({"rules": ex["rules"], "path": ex["path"], "max_steps": 100,
              "follow_redirects": True})
    data = r.json()
    if ex["id"] == "errors":
        check(f"示例[{ex['id']}] 报出错误", data["ok"] is False and len(data["errors"]) >= 6,
              f"{len(data.get('errors', []))} 个错误")
    else:
        ok = data["ok"] and data["result"]["status"] in (200, 301, 302, 303, 307, 308, 508, 509)
        check(f"示例[{ex['id']}] 执行完成", ok,
              f"status={data.get('result', {}).get('status')}")

# 循环示例必须真的触发 508
loop_ex = next(e for e in EXAMPLES if e["id"] == "loop")
data = post({"rules": loop_ex["rules"], "path": loop_ex["path"]}).json()
check("循环示例触发 508", data["result"]["status"] == 508)

# ---------------------------------------------------------------------------
# 10. 规则精简
# ---------------------------------------------------------------------------

section("规则精简")


def minimize(body):
    return client.post("/api/minimize", json=body)


def result_key_of(sim):
    r = sim["result"]
    return (r["status"], r["final_url"], r["location"], r["finish_reason"])


def verify_1_minimal(minimized_src, paths, max_steps=100, follow=False):
    """精简结果中任一剩余规则被单独删除，都会改变至少一条路径的结果。"""
    base_rules, errs = parse_rules(minimized_src)
    assert not errs
    baseline = {}
    for p in paths:
        baseline[p] = result_key_of(
            simulate(base_rules, p, max_steps=max_steps, follow_redirects=follow)
        )
    lines = minimized_src.splitlines()
    for rule in base_rules:
        trial = "\n".join(ln for i, ln in enumerate(lines, 1) if i != rule.line)
        t_rules, _ = parse_rules(trial)
        changed = any(
            result_key_of(
                simulate(t_rules, p, max_steps=max_steps, follow_redirects=follow)
            )
            != baseline[p]
            for p in paths
        )
        if not changed:
            return rule.line
    return None


# 基本精简：冗余行删除、注释与空行按原文保留
src = (
    "# 头部注释：API 版本迁移\n"
    "REWRITE ^/api/v1/(.*)$ /api/legacy/$1 next\n"
    "\n"
    "# 冗余规则：目标路径不会命中\n"
    "REWRITE ^/unused/(.*)$ /x/$1 next\n"
    "REDIRECT ^/old$ /new 301\n"
    "# 尾部注释\n"
)
r = minimize({"rules": src, "paths": ["/api/v1/users"], "max_steps": 100,
              "follow_redirects": False})
check("精简 POST 200", r.status_code == 200, r.text)
data = r.json()
check("精简 ok", data["ok"] is True, data)
check("删除第 5、6 行（原行号）", data["removed_lines"] == [5, 6], data["removed_lines"])
check("保留第 2 行", data["kept_lines"] == [2], data["kept_lines"])
check("规则数 3→1", data["rule_count_before"] == 3 and data["rule_count_after"] == 1)
check("精简完成标记", data["completed"] is True)
expected = (
    "# 头部注释：API 版本迁移\n"
    "REWRITE ^/api/v1/(.*)$ /api/legacy/$1 next\n"
    "\n"
    "# 冗余规则：目标路径不会命中\n"
    "# 尾部注释"
)
check("注释与空行按原文保留", data["minimized_rules"] == expected,
      repr(data["minimized_rules"]))
check("路径前后结果一致", all(p["unchanged"] for p in data["paths"]))
p0 = data["paths"][0]
check("before 结果正确", p0["before"]["final_url"] == "/api/legacy/users"
      and p0["before"]["status"] == 200, p0)
check("after 四元组一致", p0["after"]["final_url"] == "/api/legacy/users"
      and p0["after"]["status"] == 200
      and p0["after"]["finish_reason"] == "fallthrough")
re_rules, re_errs = parse_rules(data["minimized_rules"])
check("精简结果可重新解析", not re_errs and len(re_rules) == data["rule_count_after"])
bad = verify_1_minimal(data["minimized_rules"], ["/api/v1/users"])
check("结果达到 1-minimal", bad is None, f"第 {bad} 行仍可删")

# 确定性：同一输入两次运行结果相同
d1 = minimize({"rules": src, "paths": ["/api/v1/users"]}).json()
d2 = minimize({"rules": src, "paths": ["/api/v1/users"]}).json()
check("删除行号确定", d1["removed_lines"] == d2["removed_lines"])
check("精简文本确定", d1["minimized_rules"] == d2["minimized_rules"])

# 多路径：各自依赖的规则都保留，仅删与所有路径无关的规则
src2 = (
    "REWRITE ^/a/(.*)$ /x/$1 next\n"
    "REWRITE ^/b/(.*)$ /y/$1 next\n"
    "REWRITE ^/c/(.*)$ /z/$1 next\n"
)
data = minimize({"rules": src2, "paths": ["/a/1", "/b/2"]}).json()
check("仅删除与路径无关的第 3 行", data["removed_lines"] == [3], data["removed_lines"])
check("两条路径均一致", all(p["unchanged"] for p in data["paths"]))
bad = verify_1_minimal(data["minimized_rules"], ["/a/1", "/b/2"])
check("多路径结果 1-minimal", bad is None, f"第 {bad} 行仍可删")

# 跟随重定向选项沿用：重定向链上的规则都不可删
src3 = (
    "REDIRECT ^/promo$ /promo/spring 302\n"
    "REWRITE ^/promo/spring$ /pages/promo next\n"
    "REWRITE ^/other$ /else next\n"
)
data = minimize({"rules": src3, "paths": ["/promo"], "follow_redirects": True}).json()
check("重定向链规则保留", data["removed_lines"] == [3], data["removed_lines"])
p = data["paths"][0]
check("跟随结果 200 /pages/promo", p["before"]["final_url"] == "/pages/promo"
      and p["before"]["status"] == 200 and p["before"]["hops"] == 1, p)
check("after 与 before 一致", p["unchanged"] and p["after"]["hops"] == 1)

# 循环路径：508 与结束原因必须保持，循环规则不可删
src4 = (
    "REWRITE ^/a/(.*)$ /b/$1 last\n"
    "REWRITE ^/b/(.*)$ /a/$1 last\n"
    "REWRITE ^/unused$ /x next\n"
)
data = minimize({"rules": src4, "paths": ["/a/x"]}).json()
check("循环规则全部保留", data["removed_lines"] == [3], data["removed_lines"])
check("508 与 loop 保持", data["paths"][0]["after"]["status"] == 508
      and data["paths"][0]["after"]["finish_reason"] == "loop")

# 全部规则均可删（目标路径不命中任何规则）
data = minimize({"rules": "REWRITE ^/x$ /y next\nREDIRECT ^/z$ /w 301\n",
                 "paths": ["/unrelated"]}).json()
check("全部规则可删", data["removed_lines"] == [1, 2] and data["rule_count_after"] == 0)
check("精简后为空文本", data["minimized_rules"] == "")

# 无有效规则（全注释）：ok 且原文保留
data = minimize({"rules": "# 只有注释\n\n", "paths": ["/a"]}).json()
check("无有效规则 ok", data["ok"] is True and data["removed_lines"] == [])
check("无规则时原文保留", data["minimized_rules"] == "# 只有注释\n")

# 重复路径去重
data = minimize({"rules": "REWRITE ^/a$ /b next\n", "paths": ["/a", "/a", "/c"]}).json()
check("重复路径去重", len(data["paths"]) == 2, len(data["paths"]))

# 规则语法错误：ok=False 且带行列定位
r = minimize({"rules": "REWRITE ^/([0-9 /x next", "paths": ["/a"]})
data = r.json()
check("语法错误 200 + ok=False", r.status_code == 200 and data["ok"] is False)
check("错误带行列定位", data["errors"][0]["line"] >= 1 and data["errors"][0]["col"] >= 1)

# 路径与参数校验
check("空路径列表 422", minimize({"rules": "", "paths": []}).status_code == 422)
check("路径缺斜杠 422", minimize({"rules": "", "paths": ["no-slash"]}).status_code == 422)
check("路径含空格 422", minimize({"rules": "", "paths": ["/a b"]}).status_code == 422)
check("路径超 20 条 422",
      minimize({"rules": "", "paths": [f"/p{i}" for i in range(21)]}).status_code == 422)
check("步数越界 422",
      minimize({"rules": "", "paths": ["/a"], "max_steps": 0}).status_code == 422)

# 有效规则数超限：明确拒绝
many = "\n".join(f"REWRITE ^/r{i}$ /x next" for i in range(301))
data = minimize({"rules": many, "paths": ["/a"]}).json()
check("规则过多被拒绝", data["ok"] is False and "过多" in data["message"], data.get("message"))

print(f"\n{'='*50}")
print(f"通过 {PASS}，失败 {FAIL}")
sys.exit(1 if FAIL else 0)
