"""规则精简：在多条请求路径的最终模拟结果不变的前提下，贪心删除规则行。

删除策略（确定性，不追求全局最短）：

- 只删除解析出的「完整有效规则」所在行；注释、空行、报错行一律原样保留，
  其余行的原文顺序不变。
- 每轮按【原行号从小到大】逐条试探：把该行从当前文本中删掉后**重新解析**
  整个规则文本，并对全部待保障路径重新模拟；若每条路径的最终状态码、
  最终 URL、Location 与结束原因都与原始结果一致，则接受本次删除。
- 反复扫描，直到某一轮没有任何规则可删——此时剩余规则中任取一条单独
  删除，都会改变至少一条路径的结果。
"""

from __future__ import annotations

from .engine import parse_rules
from .simulator import simulate

# 精简前后必须保持一致的四个最终结果字段：
# 最终状态码、最终 URL、Location、结束原因
SIGNATURE_FIELDS = ("status", "final_url", "location", "finish_reason")

MAX_PATHS = 50
MAX_PATH_LENGTH = 4096
MAX_RULE_LINES = 400


class MinimizeValidationError(ValueError):
    """输入本身不合法（区别于规则语法错误），消息可直接展示给用户。"""


def normalize_paths(paths: list[str]) -> list[str]:
    """校验路径列表，去首尾空白并按首次出现顺序去重。"""
    if not isinstance(paths, list) or not paths:
        raise MinimizeValidationError("请至少输入一条待保障的请求路径")

    cleaned: list[str] = []
    seen: set[str] = set()
    for idx, raw in enumerate(paths, start=1):
        if not isinstance(raw, str):
            raise MinimizeValidationError(f"第 {idx} 条路径不是文本")
        value = raw.strip()
        if not value:
            raise MinimizeValidationError(f"第 {idx} 条请求路径为空")
        if len(value) > MAX_PATH_LENGTH:
            raise MinimizeValidationError(
                f"第 {idx} 条请求路径过长（超过 {MAX_PATH_LENGTH} 个字符）"
            )
        if not value.startswith("/"):
            raise MinimizeValidationError(
                f"第 {idx} 条请求路径必须以 / 开头：{value}"
            )
        if any(ch in value for ch in (" ", "\n", "\r", "\t")):
            raise MinimizeValidationError(
                f"第 {idx} 条请求路径不能包含空白字符：{value}"
            )
        if value not in seen:
            seen.add(value)
            cleaned.append(value)

    if len(cleaned) > MAX_PATHS:
        raise MinimizeValidationError(
            f"待保障路径最多 {MAX_PATHS} 条（当前去重后 {len(cleaned)} 条）"
        )
    return cleaned


def _signature(result: dict) -> tuple:
    """提取用于比较的最终结果指纹。"""
    return tuple(result[key] for key in SIGNATURE_FIELDS)


def _error_dicts(errors) -> list[dict]:
    return [
        {
            "line": e.line,
            "col": e.col,
            "length": e.length,
            "message": e.message,
            "source": e.source,
        }
        for e in errors
    ]


def minimize_rules(
    source: str,
    paths: list[str],
    max_steps: int = 100,
    follow_redirects: bool = False,
) -> dict:
    """执行贪心精简，返回可直接序列化的结果字典。

    规则文本有语法错误时返回 ``{"ok": False, "errors": [...]}``；
    路径列表 / 规则数量不合法时抛出 :class:`MinimizeValidationError`。
    """
    paths = normalize_paths(paths)

    rules, errors = parse_rules(source)
    if errors:
        return {"ok": False, "errors": _error_dicts(errors)}

    if len(rules) > MAX_RULE_LINES:
        raise MinimizeValidationError(
            f"参与精简的有效规则不能超过 {MAX_RULE_LINES} 条"
            f"（当前 {len(rules)} 条），请缩小规则文本范围后重试"
        )

    rule_lines = [rule.line for rule in rules]
    # keepends=True：重建文本时未删除行（含其换行符）可逐字节保留
    source_lines = source.splitlines(keepends=True)

    def render(removed: set[int]) -> str:
        return "".join(
            text
            for lineno, text in enumerate(source_lines, start=1)
            if lineno not in removed
        )

    def summarize(rule_list: list) -> list[dict]:
        """对全部路径模拟一遍，返回完整最终结果（精简前 / 精简后各调用一次）。"""
        summaries: list[dict] = []
        for path in paths:
            summaries.append(
                simulate(
                    rule_list,
                    path,
                    max_steps=max_steps,
                    follow_redirects=follow_redirects,
                )["result"]
            )
        return summaries

    baseline = summarize(rules)
    baseline_sigs = [_signature(item) for item in baseline]

    def deletion_is_safe(trial_rules: list) -> bool:
        """删行后重新解析得到的规则集，是否让全部路径结果保持不变。

        按路径顺序逐条模拟，第一条出现差异的路径即可判定不安全，
        避免对注定失败的试探做多余模拟。
        """
        for idx, path in enumerate(paths):
            summary = simulate(
                trial_rules,
                path,
                max_steps=max_steps,
                follow_redirects=follow_redirects,
            )["result"]
            if _signature(summary) != baseline_sigs[idx]:
                return False
        return True

    # 按原行号确定性地反复扫描；每接受一次删除就从删后的文本继续试探
    removed: set[int] = set()
    attempts = 0
    while True:
        progressed = False
        for line_no in rule_lines:
            if line_no in removed:
                continue
            candidate = removed | {line_no}
            # 每次删行后都【重新解析】删后文本再模拟
            trial_rules, trial_errors = parse_rules(render(candidate))
            attempts += 1
            # 删除完整有效规则行理论上不会引入语法错误；保守起见，
            # 一旦重新解析报错就放弃本次删除。
            if trial_errors:
                continue
            if deletion_is_safe(trial_rules):
                removed = candidate
                progressed = True
        if not progressed:
            break

    minimized_text = render(removed)
    final_rules, _ = parse_rules(minimized_text)
    final = summarize(final_rules)

    return {
        "ok": True,
        "paths": paths,
        "removed_lines": sorted(removed),
        "minimized_rules": minimized_text,
        "original_rule_count": len(rules),
        "minimized_rule_count": len(final_rules),
        "attempts": attempts,
        "results": [
            {
                "path": path,
                "before": before,
                "after": after,
                "unchanged": _signature(before) == _signature(after),
            }
            for path, before, after in zip(paths, baseline, final)
        ],
    }
