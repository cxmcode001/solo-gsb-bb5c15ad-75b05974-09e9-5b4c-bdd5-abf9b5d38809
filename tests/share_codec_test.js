// ShareCodec 纯函数单测：node tests/share_codec_test.js

const assert = require("assert");
const codec = require("../app/static/share.js");

let pass = 0;
function check(name, fn) {
  try {
    fn();
    pass += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    console.error(`  ✗ ${name}\n    ${err.stack}`);
    process.exitCode = 1;
  }
}
function eq(actual, expected) {
  assert.strictEqual(actual, expected);
}
function deepEq(actual, expected) {
  assert.deepStrictEqual(actual, expected);
}

console.log("=== 正常往返 ===");

const state = {
  rules: "# 中文注释\nREWRITE ^/a/(.*)$ /b/$1 last\nREDIRECT ^/h$ /x 301\n",
  path: "/a/42?q=中&x=1",
  max_steps: 50,
  follow_redirects: true,
};

let encoded;
check("编码得到 v1=<base64url>，且 payload 不含 +/=", () => {
  encoded = codec.encodeShareState(state);
  assert.match(encoded, /^v1=[A-Za-z0-9_-]+$/);
  const payload = encoded.slice(3); // 去掉 "v1=" 分隔符
  assert.ok(!payload.includes("="));
});

check("# + 编码结果可完整往返（含 UTF-8 中文）", () => {
  const r = codec.parseFragment("#" + encoded);
  eq(r.status, "ok");
  deepEq(r.state, state);
});

check("不带 # 也能解析", () => {
  eq(codec.parseFragment(encoded).status, "ok");
});

check("URL 片段只含 ASCII（不会携带裸规则文本到服务端日志）", () => {
  assert.ok(/^[\x20-\x7e]+$/.test(encoded));
});

check("payload 可被 encodeURIComponent 原样保留（无保留字符）", () => {
  const p = encoded.slice(3);
  eq(encodeURIComponent(p), p);
});

console.log("=== 非分享片段静默忽略 ===");

check("无片段 -> empty", () => eq(codec.parseFragment("").status, "empty"));
check("只有 # -> empty", () => eq(codec.parseFragment("#").status, "empty"));
check("普通锚点 -> ignored", () => eq(codec.parseFragment("#section-1").status, "ignored"));
check("其它查询式片段 -> ignored", () => eq(codec.parseFragment("#foo=bar").status, "ignored"));

console.log("=== 未知版本 ===");

check("v9 版本 -> bad_version", () => {
  const r = parse("v9=e30");
  eq(r.status, "error");
  eq(r.code, "bad_version");
  assert.match(r.message, /v9/);
});

check("v0 版本 -> bad_version", () => {
  eq(parse("v0=e30").code, "bad_version");
});

check("前导零 v01 不被当作 v1", () => {
  eq(parse("v01=" + encoded.slice(3)).code, "bad_version");
});

check("payload 内 v=2 -> bad_version", () => {
  const r = encodeJson({ v: 2, rules: "", path: "/", max_steps: 1, follow_redirects: false });
  eq(r.code, "bad_version");
});

console.log("=== 损坏数据 ===");

check("空 payload -> bad_payload", () => eq(parse("v1=").code, "bad_payload"));
check("payload 含非法 base64url 字符（.）-> bad_payload", () =>
  eq(parse("v1=e3.0").code, "bad_payload"));
check("payload 含等号 -> bad_payload", () =>
  eq(parse("v1=e30=").code, "bad_payload"));
check("base64 长度 % 4 == 1 -> bad_payload", () =>
  eq(parse("v1=A").code, "bad_payload"));
check("payload 不是 JSON -> bad_payload", () =>
  eq(parse("v1=" + b64url("not json{")).code, "bad_payload"));
check("JSON 根为数组 -> bad_state", () =>
  eq(encodeJson([]).code, "bad_state"));
check("JSON 根为字符串 -> bad_state", () =>
  eq(encodeJson("x").code, "bad_state"));
check("非法 UTF-8 字节 -> bad_payload", () => {
  // 0xff 0xfe 不是合法 UTF-8 序列
  const bad = b64urlBytes([0x7b, 0xff, 0xfe, 0x7d]);
  eq(parse("v1=" + bad).code, "bad_payload");
});

console.log("=== 非法字段 ===");

const base = { v: 1, rules: "REWRITE ^/a$ /b next", path: "/a", max_steps: 10, follow_redirects: false };

check("出现未知字段 -> bad_state", () =>
  eq(encodeJson({ ...base, evil: 1 }).code, "bad_state"));
check("缺少版本号 -> bad_version", () => {
  const { v, ...noV } = base;
  eq(encodeJson(noV).code, "bad_version");
});
check("rules 非字符串 -> bad_state", () =>
  eq(encodeJson({ ...base, rules: 5 }).code, "bad_state"));
check("path 不以 / 开头 -> bad_state", () =>
  eq(encodeJson({ ...base, path: "a" }).code, "bad_state"));
check("path 含空格 -> bad_state", () =>
  eq(encodeJson({ ...base, path: "/a b" }).code, "bad_state"));
check("path 为空 -> bad_state", () =>
  eq(encodeJson({ ...base, path: "" }).code, "bad_state"));
check("max_steps=0 -> bad_state", () =>
  eq(encodeJson({ ...base, max_steps: 0 }).code, "bad_state"));
check("max_steps=501 -> bad_state", () =>
  eq(encodeJson({ ...base, max_steps: 501 }).code, "bad_state"));
check("max_steps 为小数 -> bad_state", () =>
  eq(encodeJson({ ...base, max_steps: 1.5 }).code, "bad_state"));
check("max_steps 为字符串 -> bad_state", () =>
  eq(encodeJson({ ...base, max_steps: "10" }).code, "bad_state"));
check("follow_redirects 非布尔 -> bad_state", () =>
  eq(encodeJson({ ...base, follow_redirects: 1 }).code, "bad_state"));
check("缺 rules 字段 -> bad_state", () => {
  const { rules, ...rest } = base;
  eq(encodeJson(rest).code, "bad_state");
});
check("rules 超过长度上限 -> bad_state", () =>
  eq(encodeJson({ ...base, rules: "x".repeat(codec.MAX_RULES_LENGTH + 1) }).code, "bad_state"));
check("所有错误都给出可见中文提示", () => {
  const r = encodeJson({ ...base, path: "bad" });
  assert.ok(r.message.length > 5);
});

console.log("=== 编码侧防御 ===");

check("编码未知字段直接抛错", () => {
  assert.throws(() => codec.encodeShareState({ ...state, v: 1 }));
  assert.throws(() => codec.encodeShareState({ ...state, extra: 1 }));
});
check("编码非法 max_steps 抛错", () => {
  assert.throws(() => codec.encodeShareState({ ...state, max_steps: 0 }));
});
check("大规则文本可正确往返", () => {
  const big = "REWRITE ^/x$ /y next\n".repeat(4000);
  const enc = codec.encodeShareState({
    rules: big, path: "/x", max_steps: 500, follow_redirects: false,
  });
  const r = codec.parseFragment(enc);
  eq(r.status, "ok");
  eq(r.state.rules, big);
  eq(r.state.max_steps, 500);
});

console.log(`\n通过 ${pass}`);

// ---------------------------------------------------------------------------

function parse(h) {
  return codec.parseFragment(h);
}
function b64url(text) {
  return Buffer.from(text, "binary")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
function b64urlBytes(bytes) {
  return Buffer.from(bytes)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
function encodeJson(obj) {
  return parse("v1=" + b64url(Buffer.from(JSON.stringify(obj), "utf8").toString("binary")));
}
