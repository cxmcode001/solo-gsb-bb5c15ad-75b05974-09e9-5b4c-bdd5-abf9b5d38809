"use strict";

// ---------------------------------------------------------------------------
// 场景分享：状态编解码（纯函数，无 DOM 依赖，可在 Node 下单测）
//
// 链接形如：
//   https://host/path#v1=<Base64URL(JSON)>
//
// 状态保存在 URL 片段（fragment）中，浏览器不会把片段发给服务端；
// JSON 以 UTF-8 编码后再做 Base64URL，v1 为当前版本号。
// ---------------------------------------------------------------------------

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.ShareCodec = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const SHARE_VERSION = 1;

  // 防御性长度上限，避免粘贴超大链接时撑爆编辑器 / 内存
  const MAX_RULES_LENGTH = 200000;
  const MAX_PATH_LENGTH = 4096;

  const FIELD_KEYS = ["rules", "path", "max_steps", "follow_redirects"];
  const FIELD_KEYS_SET = new Set(FIELD_KEYS);
  const PAYLOAD_KEYS_SET = new Set(["v", ...FIELD_KEYS]);

  const UTF8_DECODER = new TextDecoder("utf-8", { fatal: true });

  class ShareError extends Error {
    constructor(code, message) {
      super(message);
      this.name = "ShareError";
      this.code = code;
    }
  }

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function error(code, message) {
    return { status: "error", code, message };
  }

  // 校验四个业务字段并返回规范化对象；非法即抛 ShareError
  function checkFields(obj) {
    const { rules, path, max_steps, follow_redirects } = obj;

    if (typeof rules !== "string" || rules.length > MAX_RULES_LENGTH) {
      throw new ShareError(
        "bad_state",
        "分享链接中的规则文本缺失或过长，已保留默认界面。"
      );
    }
    if (
      typeof path !== "string" ||
      path.length === 0 ||
      path.length > MAX_PATH_LENGTH ||
      !path.startsWith("/") ||
      /\s/.test(path)
    ) {
      throw new ShareError(
        "bad_state",
        "分享链接中的请求路径不合法（需以 / 开头且不含空白），已保留默认界面。"
      );
    }
    if (!Number.isInteger(max_steps) || max_steps < 1 || max_steps > 500) {
      throw new ShareError(
        "bad_state",
        "分享链接中的步数上限需为 1–500 的整数，已保留默认界面。"
      );
    }
    if (typeof follow_redirects !== "boolean") {
      throw new ShareError(
        "bad_state",
        "分享链接中的跟随重定向选项不是布尔值，已保留默认界面。"
      );
    }

    return { rules, path, max_steps, follow_redirects };
  }

  // 输入 { rules, path, max_steps, follow_redirects } -> "v1=xxxx"
  function encodeShareState(input) {
    if (!isPlainObject(input)) {
      throw new ShareError("bad_state", "待分享状态必须是对象。");
    }
    for (const key of Object.keys(input)) {
      if (!FIELD_KEYS_SET.has(key)) {
        throw new ShareError("bad_state", `待分享状态包含非法字段：${key}`);
      }
    }
    const state = checkFields(input);
    const json = JSON.stringify({ v: SHARE_VERSION, ...state });
    const bytes = new TextEncoder().encode(json);

    // 分块转二进制串，避免大规则文本时 String.fromCharCode 参数过多
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    const b64url = btoa(binary)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    return `v${SHARE_VERSION}=${b64url}`;
  }

  // 解码片段内部已经是 JSON 对象的状态
  function decodeState(obj) {
    if (!isPlainObject(obj)) {
      throw new ShareError("bad_state", "分享链接中的状态不是有效对象，已保留默认界面。");
    }
    for (const key of Object.keys(obj)) {
      if (!PAYLOAD_KEYS_SET.has(key)) {
        throw new ShareError(
          "bad_state",
          `分享链接包含非法字段（${key}），已保留默认界面。`
        );
      }
    }
    if (obj.v !== SHARE_VERSION) {
      throw new ShareError(
        "bad_version",
        obj.v === undefined
          ? "分享链接缺少版本号，已保留默认界面。"
          : `不支持的分享链接版本（v${obj.v}），请使用新版调试台打开。`
      );
    }
    return checkFields(obj);
  }

  // 解析 location.hash：
  //   {status:"empty"}                    无片段
  //   {status:"ignored"}                  不是本功能的链接（不要打扰用户）
  //   {status:"error", code, message}     损坏 / 版本未知 / 字段非法
  //   {status:"ok", state}                恢复出的编辑状态
  function parseFragment(hash) {
    let fragment = typeof hash === "string" ? hash : "";
    if (fragment.startsWith("#")) fragment = fragment.slice(1);
    if (fragment === "") return { status: "empty" };

    const match = /^v(\d+)(?:=(.*))?$/.exec(fragment);
    if (!match) {
      // 可能是页内锚点等其它片段，不属于分享链接，静默忽略
      return { status: "ignored" };
    }

    const version = match[1];
    const payload = match[2];
    if (version !== String(SHARE_VERSION)) {
      return error(
        "bad_version",
        `不支持的分享链接版本（v${version}），请使用新版调试台打开，已保留默认界面。`
      );
    }
    if (payload === undefined || payload === "" || !/^[A-Za-z0-9_-]+$/.test(payload)) {
      return error("bad_payload", "分享链接数据已损坏或被截断，已保留默认界面。");
    }

    let json;
    try {
      const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
      const mod = base64.length % 4;
      if (mod === 1) throw new Error("invalid base64url length");
      const padded = mod ? base64 + "=".repeat(4 - mod) : base64;
      const binary = atob(padded);
      const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
      json = UTF8_DECODER.decode(bytes); // fatal：非法 UTF-8 直接抛错
    } catch (err) {
      return error("bad_payload", "分享链接数据已损坏或被截断，已保留默认界面。");
    }

    let obj;
    try {
      obj = JSON.parse(json);
    } catch (err) {
      return error("bad_payload", "分享链接数据不是合法 JSON，已保留默认界面。");
    }

    try {
      return { status: "ok", state: decodeState(obj) };
    } catch (err) {
      if (err instanceof ShareError) return error(err.code, err.message);
      return error("bad_payload", "分享链接数据已损坏，已保留默认界面。");
    }
  }

  return {
    SHARE_VERSION,
    MAX_RULES_LENGTH,
    MAX_PATH_LENGTH,
    ShareError,
    encodeShareState,
    parseFragment,
  };
});
