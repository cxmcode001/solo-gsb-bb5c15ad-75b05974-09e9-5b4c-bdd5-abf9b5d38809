"use strict";

// ---------------------------------------------------------------------------
// DOM 引用
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

const editor = $("rulesEditor");
const gutter = $("gutter");
const editorWrap = $("editorWrap");
const errorList = $("errorList");
const pathInput = $("pathInput");
const maxStepsInput = $("maxStepsInput");
const followRedirectsInput = $("followRedirectsInput");
const runBtn = $("runBtn");
const copyBtn = $("copyBtn");
const shareBtn = $("shareBtn");
const ruleCount = $("ruleCount");
const exampleSelect = $("exampleSelect");
const loadExampleBtn = $("loadExampleBtn");
const shareNotice = $("shareNotice");
const shareNoticeText = $("shareNoticeText");
const shareNoticeClose = $("shareNoticeClose");

const summary = $("summary");
const statusBadge = $("statusBadge");
const finalUrl = $("finalUrl");
const finishReason = $("finishReason");
const stepMeta = $("stepMeta");
const locationLine = $("locationLine");
const locationValue = $("locationValue");
const traceEl = $("trace");
const toastEl = $("toast");

const minimizePathsInput = $("minimizePathsInput");
const minimizeBtn = $("minimizeBtn");
const minimizeCount = $("minimizeCount");
const minimizeModal = $("minimizeModal");
const minimizeModalClose = $("minimizeModalClose");
const minimizeModalBody = $("minimizeModalBody");

let lastResponse = null;
let examples = [];
let errorLineSet = new Set();

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

let toastTimer = null;
function toast(message, isError = false) {
  toastEl.textContent = message;
  toastEl.className = isError ? "toast error" : "toast";
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 2200);
}

async function copyText(text, successMessage = "已复制到剪贴板") {
  try {
    await navigator.clipboard.writeText(text);
    toast(successMessage);
  } catch (err) {
    // 降级：临时 textarea + execCommand
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      toast(successMessage);
    } catch (e) {
      toast("复制失败，请手动选择文本", true);
    }
    document.body.removeChild(ta);
  }
}

// ---------------------------------------------------------------------------
// 编辑器行号
// ---------------------------------------------------------------------------

function renderGutter() {
  const lines = editor.value.split("\n").length;
  let html = "";
  for (let i = 1; i <= lines; i++) {
    if (i > 1) html += "\n";
    html += errorLineSet.has(i)
      ? `<span class="line-error">${i}</span>`
      : String(i);
  }
  gutter.innerHTML = html;
}

editor.addEventListener("input", () => {
  errorLineSet = new Set();
  editor.classList.remove("has-error-line");
  errorList.hidden = true;
  renderGutter();
});

editor.addEventListener("scroll", () => {
  gutter.scrollTop = editor.scrollTop;
  gutter.scrollLeft = editor.scrollLeft;
});

// Tab 键插入两个空格而非跳出编辑器
editor.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    editor.value =
      editor.value.slice(0, start) + "  " + editor.value.slice(end);
    editor.selectionStart = editor.selectionEnd = start + 2;
  }
});

// ---------------------------------------------------------------------------
// 示例载入
// ---------------------------------------------------------------------------

async function loadExamples() {
  try {
    const res = await fetch("/api/examples");
    examples = await res.json();
  } catch (err) {
    examples = [];
    return;
  }
  exampleSelect.innerHTML = "";
  examples.forEach((ex) => {
    const opt = document.createElement("option");
    opt.value = ex.id;
    opt.textContent = ex.name;
    exampleSelect.appendChild(opt);
  });
}

loadExampleBtn.addEventListener("click", () => {
  const ex = examples.find((x) => x.id === exampleSelect.value);
  if (!ex) return;
  editor.value = ex.rules;
  pathInput.value = ex.path;
  errorLineSet = new Set();
  editor.classList.remove("has-error-line");
  errorList.hidden = true;
  renderGutter();
  toast(`已载入示例：${ex.name}`);
});

// ---------------------------------------------------------------------------
// 错误定位
// ---------------------------------------------------------------------------

function jumpToError(err) {
  const lines = editor.value.split("\n");
  const rawLine = lines[err.line - 1] ?? "";
  // 服务端的列基于「去掉首尾空白与注释」后的行，需要加回首列空白偏移
  const lead = rawLine.length - rawLine.trimStart().length;
  let offset = 0;
  for (let i = 0; i < err.line - 1; i++) offset += lines[i].length + 1;
  const col = Math.max(0, err.col - 1);
  const start = offset + lead + col;
  const length = Math.max(1, err.length || 1);

  editor.focus();
  editor.setSelectionRange(start, Math.min(start + length, editor.value.length));
  // 滚动到目标行
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
  editor.scrollTop = Math.max(0, (err.line - 1) * lineHeight - 40);
}

function renderErrors(errors) {
  errorList.innerHTML = "";
  errorLineSet = new Set();

  errors.forEach((err) => {
    errorLineSet.add(err.line);
    const item = el("div", "error-item");
    const pos = el("span", "error-pos", `第 ${err.line} 行 第 ${err.col} 列`);
    const msg = el("span", "error-msg", err.message);
    item.append(pos, msg);
    item.title = "点击跳转到错误位置";
    item.addEventListener("click", () => jumpToError(err));
    errorList.appendChild(item);
  });

  errorList.hidden = false;
  editor.classList.add("has-error-line");
  renderGutter();
}

// ---------------------------------------------------------------------------
// 结果渲染
// ---------------------------------------------------------------------------

const REASON_TEXT = {
  fallthrough: "全部规则放行（200）",
  redirect: "命中重定向，规则链终止",
  loop: "检测到改写循环（508）",
  step_limit: "超过步数上限（509）",
  hop_limit: "重定向跳数过多",
};

function statusClass(status) {
  if (status >= 200 && status < 300) return "s-2xx";
  if (status >= 300 && status < 400) return "s-3xx";
  if (status >= 500) return "s-5xx";
  return "";
}

function renderSummary(result) {
  summary.hidden = false;
  statusBadge.textContent = `HTTP ${result.status}`;
  statusBadge.className = `status-badge ${statusClass(result.status)}`;
  finalUrl.textContent = result.final_url || "—";
  finishReason.textContent = REASON_TEXT[result.finish_reason] || result.finish_reason;
  stepMeta.textContent = `执行 ${result.steps} 步${
    result.hops ? ` · 跟随重定向 ${result.hops} 跳` : ""
  }`;

  if (result.location) {
    locationLine.hidden = false;
    locationValue.textContent = result.location;
  } else {
    locationLine.hidden = true;
  }
}

function flowClassFor(type, traceStep) {
  if (type === "test") return "skip";
  if (type === "rewrite") {
    return traceStep.flow && traceStep.flow.startsWith("last") ? "last" : "next";
  }
  if (type === "redirect") return "redirect";
  if (type === "end") return "terminal";
  if (type === "loop" || type === "limit") return "danger";
  return "skip";
}

function renderGroups(groups) {
  if (!groups || !groups.length) return null;
  const wrap = el("div", "groups");
  groups.forEach((g) => {
    const chip = el("span", "group-chip");
    const label = g.name ? `$${g.index}:${g.name}` : `$${g.index}`;
    chip.appendChild(el("b", "", label + " = "));
    if (g.value === null || g.value === undefined) {
      chip.appendChild(el("span", "null-value", "未参与匹配"));
    } else {
      chip.appendChild(document.createTextNode(JSON.stringify(g.value)));
    }
    wrap.appendChild(chip);
  });
  return wrap;
}

function renderTraceStep(step) {
  const type = step.type;

  // 特殊节点：start / end / loop / limit
  if (type === "start" || type === "end" || type === "loop" || type === "limit") {
    const node = el("div", `step special ${type}`);
    const titles = {
      start: "① 接收请求",
      end: "✅ 规则链结束 — 内部放行",
      loop: "🔁 检测到循环 — 已停止",
      limit: "⏱ 超过步数上限 — 强制停止",
    };
    node.appendChild(el("div", "special-title", titles[type]));
    const note = el("div", "step-note", step.note);
    node.appendChild(note);
    if (step.path) {
      const pathRow = el("div", "step-path");
      pathRow.appendChild(el("span", "", "当前路径："));
      pathRow.appendChild(el("span", "path-new", step.path));
      node.appendChild(pathRow);
    }
    return node;
  }

  const node = el("div", `step ${type === "test" ? "skip-step" : ""}`);

  // 头部：序号 + 规则原文 + 行号 + 流向标签
  const head = el("div", "step-head");
  head.appendChild(el("span", "step-seq", String(step.seq)));
  if (step.rule) {
    head.appendChild(el("span", "step-rule", step.rule));
    head.appendChild(el("span", "step-line", `（第 ${step.line} 行，规则 #${step.rule_index + 1}）`));
  }
  const flow = el("span", `flow ${flowClassFor(type, step)}`, step.flow || "");
  head.appendChild(flow);
  node.appendChild(head);

  // 路径变化
  if (type === "rewrite") {
    const pathRow = el("div", "step-path");
    pathRow.appendChild(el("span", "path-old", step.path));
    pathRow.appendChild(el("span", "arrow", "→"));
    pathRow.appendChild(el("span", "path-new", step.new_path));
    node.appendChild(pathRow);
  } else if (type === "redirect") {
    const pathRow = el("div", "step-path");
    pathRow.appendChild(el("span", "", `HTTP ${step.status} → Location: `));
    pathRow.appendChild(el("span", "path-new", step.location));
    node.appendChild(pathRow);
  }

  // 捕获组变量
  const groupsNode = renderGroups(step.groups);
  if (groupsNode) node.appendChild(groupsNode);

  node.appendChild(el("div", "step-note", step.note));
  return node;
}

function renderTrace(trace) {
  traceEl.innerHTML = "";
  const frag = document.createDocumentFragment();
  trace.forEach((step) => frag.appendChild(renderTraceStep(step)));
  traceEl.appendChild(frag);
}

function showPlaceholder(html) {
  summary.hidden = true;
  traceEl.innerHTML = "";
  const ph = el("div", "placeholder");
  ph.innerHTML = html;
  traceEl.appendChild(ph);
}

// ---------------------------------------------------------------------------
// 调试报告（复制用纯文本）
// ---------------------------------------------------------------------------

function buildReport(data, reqBody) {
  const lines = [];
  lines.push("URL 重写规则调试结果");
  lines.push("=".repeat(52));
  lines.push(`请求路径：${reqBody.path}`);
  lines.push(
    `选项：步数上限=${reqBody.max_steps}，跟随重定向=${
      reqBody.follow_redirects ? "是" : "否"
    }`
  );
  lines.push("");

  if (!data.ok) {
    lines.push(`规则存在 ${data.errors.length} 个语法错误：`);
    data.errors.forEach((e) => {
      lines.push(`  · 第 ${e.line} 行 第 ${e.col} 列：${e.message}`);
    });
    return lines.join("\n");
  }

  const r = data.result;
  lines.push(`最终状态：HTTP ${r.status}`);
  lines.push(`最终 URL：${r.final_url}`);
  if (r.location) lines.push(`Location：${r.location}`);
  lines.push(
    `结束原因：${REASON_TEXT[r.finish_reason] || r.finish_reason}` +
      `（${r.steps} 步${r.hops ? `，${r.hops} 跳` : ""}）`
  );
  lines.push("-".repeat(52));

  data.trace.forEach((step) => {
    const prefix =
      step.seq === 0 && step.type === "start"
        ? "开始"
        : `#${step.seq}`;
    let line = `[${prefix}] ${step.note}`;
    if (step.type === "rewrite") {
      line += `  [${step.path} -> ${step.new_path}]`;
    }
    lines.push(line);
    if (step.groups) {
      const g = step.groups
        .map((x) => {
          const name = x.name ? `:${x.name}` : "";
          const val = x.value === null ? "<未参与>" : JSON.stringify(x.value);
          return `$${x.index}${name}=${val}`;
        })
        .join("  ");
      lines.push(`        捕获组：${g}`);
    }
  });

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// 场景分享（状态保存在 URL 片段，不发送到服务端）
// ---------------------------------------------------------------------------

function showShareNotice(message) {
  shareNoticeText.textContent = message;
  shareNotice.hidden = false;
}

shareNoticeClose.addEventListener("click", () => {
  shareNotice.hidden = true;
});

// 校验输入并收集待分享状态；不合法时提示并返回 null
function collectShareState() {
  const path = pathInput.value.trim();
  if (!path) {
    toast("请输入请求路径后再生成分享链接", true);
    pathInput.focus();
    return null;
  }
  if (!path.startsWith("/") || /\s/.test(path)) {
    toast("请求路径必须以 / 开头且不含空白", true);
    pathInput.focus();
    return null;
  }
  const maxSteps = parseInt(maxStepsInput.value, 10);
  if (!Number.isFinite(maxSteps) || maxSteps < 1 || maxSteps > 500) {
    toast("步数上限需为 1–500 之间的整数", true);
    maxStepsInput.focus();
    return null;
  }
  return {
    rules: editor.value,
    path,
    max_steps: maxSteps,
    follow_redirects: followRedirectsInput.checked,
  };
}

function resetResultPanel() {
  lastResponse = null;
  copyBtn.disabled = true;
  ruleCount.textContent = "";
  summary.hidden = true;
  traceEl.innerHTML = "";
  const ph = el("div", "placeholder");
  ph.appendChild(el("p", "", "👈 在左侧编写规则并输入请求路径"));
  ph.appendChild(
    el("p", "muted", "点击「运行调试」后，这里会按顺序展示每次匹配、捕获组变量变化与最终响应")
  );
  traceEl.appendChild(ph);
}

function applySharedState(state) {
  editor.value = state.rules;
  pathInput.value = state.path;
  maxStepsInput.value = String(state.max_steps);
  followRedirectsInput.checked = state.follow_redirects;
  errorLineSet = new Set();
  editor.classList.remove("has-error-line");
  errorList.hidden = true;
  renderGutter();
  resetResultPanel();
}

shareBtn.addEventListener("click", () => {
  const state = collectShareState();
  if (!state) return;

  let fragment;
  try {
    fragment = ShareCodec.encodeShareState(state);
  } catch (err) {
    toast(`无法生成分享链接：${err.message}`, true);
    return;
  }

  const url = `${location.origin}${location.pathname}${location.search}#${fragment}`;
  copyText(url, "已复制分享链接：打开即可恢复相同编辑状态");
  // 同步地址栏，刷新页面也能回到当前状态；片段不会发送到服务端
  history.replaceState(null, "", `#${fragment}`);
});

// 打开分享链接时恢复编辑状态；损坏 / 未知版本 / 非法字段均保留默认界面并提示
function restoreFromHash() {
  const parsed = ShareCodec.parseFragment(location.hash);
  if (parsed.status === "empty" || parsed.status === "ignored") return;

  if (parsed.status === "error") {
    showShareNotice(parsed.message);
    // 清掉损坏片段，避免刷新后反复弹错；不触碰其它页面锚点
    history.replaceState(null, "", location.pathname + location.search);
    return;
  }

  applySharedState(parsed.state);
  toast("已从分享链接恢复编辑状态，可点击「运行调试」查看结果");
}

// ---------------------------------------------------------------------------
// 运行调试
// ---------------------------------------------------------------------------

async function runDebug() {
  const path = pathInput.value.trim();
  const maxSteps = parseInt(maxStepsInput.value, 10);

  if (!path) {
    toast("请输入请求路径", true);
    pathInput.focus();
    return;
  }
  if (!path.startsWith("/")) {
    toast("请求路径必须以 / 开头", true);
    pathInput.focus();
    return;
  }
  if (!Number.isFinite(maxSteps) || maxSteps < 1 || maxSteps > 500) {
    toast("步数上限需为 1–500 之间的整数", true);
    maxStepsInput.focus();
    return;
  }

  const body = {
    rules: editor.value,
    path,
    max_steps: maxSteps,
    follow_redirects: followRedirectsInput.checked,
  };

  runBtn.disabled = true;
  runBtn.textContent = "⏳ 运行中…";

  try {
    const res = await fetch("/api/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.status === 422) {
      const payload = await res.json();
      const msg =
        payload.detail && payload.detail[0]
          ? payload.detail[0].msg
          : "请求参数不合法";
      toast(msg, true);
      return;
    }

    const data = await res.json();
    lastResponse = { data, body };
    copyBtn.disabled = false;
    ruleCount.textContent = `共解析出 ${data.rule_count} 条有效规则`;

    if (!data.ok) {
      renderErrors(data.errors || []);
      summary.hidden = true;
      traceEl.innerHTML = "";
      const ph = el("div", "placeholder");
      ph.appendChild(el("p", "", `🚧 规则存在 ${data.errors.length} 个语法错误`));
      ph.appendChild(
        el("p", "muted", "点击左下方错误信息可跳转到对应的行列，修正后重新运行")
      );
      traceEl.appendChild(ph);
      toast(`发现 ${data.errors.length} 个语法错误`, true);
      return;
    }

    errorList.hidden = true;
    errorLineSet = new Set();
    editor.classList.remove("has-error-line");
    renderGutter();
    renderSummary(data.result);
    renderTrace(data.trace);
    toast("调试完成");
  } catch (err) {
    showPlaceholder(
      `<p>❌ 无法连接调试服务</p><p class="muted">${String(err)}</p>`
    );
    toast("网络错误，服务不可达", true);
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = "▶ 运行调试";
  }
}

runBtn.addEventListener("click", runDebug);

copyBtn.addEventListener("click", () => {
  if (!lastResponse) return;
  copyText(buildReport(lastResponse.data, lastResponse.body));
});

// Ctrl/Cmd + Enter 快捷运行
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    runDebug();
  }
});

// ---------------------------------------------------------------------------
// 规则精简
// ---------------------------------------------------------------------------

// 前端先做一轮轻校验，返回错误信息（null 表示通过）
function validateMinimizePaths(lines) {
  if (!lines.length) {
    return "请输入至少一条待保障的请求路径（每行一条）";
  }
  if (lines.length > 50) {
    return `待保障路径最多 50 条（当前 ${lines.length} 条），请精简后重试`;
  }
  for (let i = 0; i < lines.length; i += 1) {
    const p = lines[i];
    if (!p.startsWith("/")) {
      return `第 ${i + 1} 条请求路径必须以 / 开头：${p}`;
    }
    if (/\s/.test(p)) {
      return `第 ${i + 1} 条请求路径不能包含空白字符：${p}`;
    }
    if (p.length > 4096) {
      return `第 ${i + 1} 条请求路径过长（超过 4096 个字符）`;
    }
  }
  return null;
}

function resultSignature(result) {
  return [
    result.status,
    result.final_url,
    result.location,
    result.finish_reason,
  ];
}

function renderMinimizePathCard(item) {
  const card = el("div", `min-path-card ${item.unchanged ? "same" : "diff"}`);

  const head = el("div", "min-path-head");
  head.appendChild(el("code", "min-path-name", item.path));
  head.appendChild(
    el(
      "span",
      `min-path-badge ${item.unchanged ? "ok" : "bad"}`,
      item.unchanged ? "✓ 结果一致" : "✗ 结果变化"
    )
  );
  card.appendChild(head);

  const grid = el("div", "min-path-grid");
  const fields = [
    ["最终状态码", (r) => `HTTP ${r.status}`],
    ["最终 URL", (r) => r.final_url || "—"],
    ["Location", (r) => r.location || "—"],
    ["结束原因", (r) => REASON_TEXT[r.finish_reason] || r.finish_reason],
  ];
  fields.forEach(([label, pick]) => {
    const beforeVal = pick(item.before);
    const afterVal = pick(item.after);
    const equal = beforeVal === afterVal;
    const cell = el("div", "min-path-field");
    cell.appendChild(el("span", "min-path-label", label));
    const vals = el("div", `min-path-vals ${equal ? "" : "changed"}`);
    const before = el("div", "min-path-val");
    before.appendChild(el("span", "min-path-tag", "前"));
    before.appendChild(el("code", "", beforeVal));
    const after = el("div", "min-path-val");
    after.appendChild(el("span", "min-path-tag", "后"));
    after.appendChild(el("code", "", afterVal));
    vals.append(before, after);
    cell.appendChild(vals);
    grid.appendChild(cell);
  });
  card.appendChild(grid);
  return card;
}

function closeMinimizeModal() {
  minimizeModal.hidden = true;
  minimizeModalBody.innerHTML = "";
}

function renderMinimizeResult(data) {
  minimizeModalBody.innerHTML = "";

  // 概要
  const summaryRow = el("div", "min-result-summary");
  const delta = data.original_rule_count - data.minimized_rule_count;
  summaryRow.appendChild(
    el(
      "strong",
      "",
      delta > 0
        ? `删除 ${delta} 条规则（${data.original_rule_count} → ${data.minimized_rule_count} 条）`
        : "没有可删除的规则：剩余每条规则单独删除都会改变至少一条路径的结果"
    )
  );
  summaryRow.appendChild(
    el(
      "span",
      "muted",
      `共保障 ${data.paths.length} 条路径 · 试探 ${data.attempts} 次（不追求全局最短）`
    )
  );
  minimizeModalBody.appendChild(summaryRow);

  // 删除的原行号
  const removedBlock = el("div", "min-block");
  removedBlock.appendChild(el("div", "min-block-title", "删除的原行号"));
  const chips = el("div", "min-removed-chips");
  if (data.removed_lines.length === 0) {
    chips.appendChild(el("span", "muted", "（无）"));
  } else {
    data.removed_lines.forEach((lineNo) => {
      const chip = el("button", "removed-chip", `第 ${lineNo} 行`);
      chip.type = "button";
      chip.title = "点击在编辑器中定位该行";
      chip.addEventListener("click", () => {
        closeMinimizeModal();
        focusEditorLine(lineNo);
      });
      chips.appendChild(chip);
    });
    const copyLinesBtn = el(
      "button",
      "btn btn-secondary btn-tiny",
      "📋 复制行号列表"
    );
    copyLinesBtn.type = "button";
    copyLinesBtn.addEventListener("click", () => {
      copyText(data.removed_lines.join(", "), "已复制删除行号列表");
    });
    chips.appendChild(copyLinesBtn);
  }
  removedBlock.appendChild(chips);
  minimizeModalBody.appendChild(removedBlock);

  // 精简后的规则文本（可复制，不覆盖编辑器）
  const rulesBlock = el("div", "min-block");
  const rulesHead = el("div", "min-block-title-row");
  rulesHead.appendChild(el("div", "min-block-title", "精简后的规则（原文顺序与注释保留）"));
  const copyRulesBtn = el("button", "btn btn-secondary btn-tiny", "📋 复制规则");
  copyRulesBtn.type = "button";
  copyRulesBtn.addEventListener("click", () => {
    copyText(data.minimized_rules, "已复制精简后的规则文本");
  });
  rulesHead.appendChild(copyRulesBtn);
  rulesBlock.appendChild(rulesHead);
  const pre = el("pre", "min-rules-view");
  pre.textContent = data.minimized_rules || "#（无规则，也无注释保留）";
  rulesBlock.appendChild(pre);
  minimizeModalBody.appendChild(rulesBlock);

  // 各路径精简前后结果
  const pathsBlock = el("div", "min-block");
  pathsBlock.appendChild(
    el("div", "min-block-title", `各路径精简前后结果（${data.results.length} 条）`)
  );
  const list = el("div", "min-path-list");
  data.results.forEach((item) => list.appendChild(renderMinimizePathCard(item)));
  pathsBlock.appendChild(list);
  minimizeModalBody.appendChild(pathsBlock);

  minimizeModal.hidden = false;
}

// 在规则编辑器中定位某一原行号（选中该行）
function focusEditorLine(lineNo) {
  const text = editor.value;
  let start = 0;
  for (let i = 1; i < lineNo; i += 1) {
    const next = text.indexOf("\n", start);
    if (next === -1) {
      start = text.length;
      break;
    }
    start = next + 1;
  }
  const nl = text.indexOf("\n", start);
  const end = nl === -1 ? text.length : nl;
  editor.focus();
  editor.setSelectionRange(start, Math.max(start, end));
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
  editor.scrollTop = Math.max(0, (lineNo - 1) * lineHeight - 40);
}

minimizeModalClose.addEventListener("click", closeMinimizeModal);
minimizeModal.addEventListener("click", (e) => {
  if (e.target === minimizeModal) closeMinimizeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !minimizeModal.hidden) closeMinimizeModal();
});

async function runMinimize() {
  const raw = minimizePathsInput.value;
  const lines = raw
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  const message = validateMinimizePaths(lines);
  if (message) {
    toast(message, true);
    minimizePathsInput.focus();
    return;
  }

  const maxSteps = parseInt(maxStepsInput.value, 10);
  if (!Number.isFinite(maxSteps) || maxSteps < 1 || maxSteps > 500) {
    toast("步数上限需为 1–500 之间的整数", true);
    maxStepsInput.focus();
    return;
  }

  const body = {
    rules: editor.value,
    paths: lines,
    max_steps: maxSteps,
    follow_redirects: followRedirectsInput.checked,
  };

  minimizeBtn.disabled = true;
  minimizeBtn.textContent = "⏳ 精简中…";
  minimizeCount.textContent = "";

  try {
    const res = await fetch("/api/minimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.status === 422 || res.status === 400) {
      const payload = await res.json();
      let msg;
      if (payload.detail && payload.detail[0]) {
        msg = payload.detail[0].msg;
      } else if (typeof payload.detail === "string") {
        msg = payload.detail;
      } else {
        msg = "请求参数不合法";
      }
      // pydantic v2 的 ValueError 消息带固定前缀，去掉以便直接展示
      msg = msg.replace(/^Value error,\s*/, "");
      toast(msg, true);
      return;
    }

    const data = await res.json();

    if (!data.ok) {
      // 原规则有误：复用错误列表定位，明确报错且不覆盖编辑器
      renderErrors(data.errors || []);
      toast(`规则存在 ${data.errors.length} 个语法错误，请先修正再精简`, true);
      return;
    }

    errorList.hidden = true;
    errorLineSet = new Set();
    editor.classList.remove("has-error-line");
    renderGutter();

    const delta = data.original_rule_count - data.minimized_rule_count;
    minimizeCount.textContent =
      delta > 0 ? `可删 ${delta} 条` : "已是最小保留集（贪心）";
    renderMinimizeResult(data);
    toast(delta > 0 ? `精简完成，删除 ${delta} 条规则` : "没有可安全删除的规则");
  } catch (err) {
    toast("网络错误，服务不可达", true);
  } finally {
    minimizeBtn.disabled = false;
    minimizeBtn.textContent = "✂️ 开始精简";
  }
}

minimizeBtn.addEventListener("click", runMinimize);

// Ctrl/Cmd + Shift + Enter 快捷精简
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "Enter") {
    e.preventDefault();
    runMinimize();
  }
});

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

renderGutter();
loadExamples();
restoreFromHash();
