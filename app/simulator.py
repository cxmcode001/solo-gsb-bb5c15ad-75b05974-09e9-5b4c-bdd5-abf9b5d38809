"""规则链逐步模拟器：顺序匹配、改写/重定向、循环检测、步数上限。"""

from __future__ import annotations

from .engine import Rule, expand

MAX_HOPS = 20  # follow_redirects 时最多跟随的重定向次数


def _is_internal_location(location: str) -> bool:
    """以单个 / 开头且不是 // 的目标，视为站内路径，可继续模拟。"""
    return location.startswith("/") and not location.startswith("//")


def _group_entries(match) -> list[dict]:
    """提取捕获组：$0 整匹配 + 数字组 + 命名组。"""
    entries: list[dict] = [{"index": 0, "name": None, "value": match.group(0)}]
    inverse = {idx: name for name, idx in match.re.groupindex.items()}
    for idx in range(1, match.re.groups + 1):
        entries.append(
            {
                "index": idx,
                "name": inverse.get(idx),
                "value": match.group(idx),  # 未参与匹配的组为 None
            }
        )
    return entries


def simulate(
    rules: list[Rule],
    path: str,
    max_steps: int = 100,
    follow_redirects: bool = False,
) -> dict:
    """执行规则链，返回完整轨迹与最终结果。

    终止状态 status：200（内部放行）、3xx（重定向）、508（检测到循环）、
    509（超过步数上限）。
    """
    trace: list[dict] = []
    steps = 0
    hops = 0
    rule_index = 0
    current = path
    # 循环检测：
    #   seen_eval —— “规则命中”的状态集合。模拟为确定性过程，若某条规则对
    #                同一路径第二次命中，后续走向必然与上次相同，即构成循环。
    #                普通的未命中顺序测试不计入，避免误报。
    seen_eval: set[tuple[str, int]] = set()
    finish_reason = "fallthrough"
    status_code = 200
    final_location = path
    location_header: str | None = None

    trace.append(
        {
            "seq": steps,
            "type": "start",
            "path": current,
            "rule_index": None,
            "line": None,
            "rule": None,
            "matched": None,
            "groups": None,
            "new_path": None,
            "status": None,
            "location": None,
            "flow": "接收请求",
            "note": "规则链开始，从第 1 条规则依次匹配",
        }
    )

    def _loop_trace(seq: int, path_value: str, idx: int) -> dict:
        return {
            "seq": seq,
            "type": "loop",
            "path": path_value,
            "rule_index": idx,
            "line": rules[idx].line if rules else None,
            "rule": rules[idx].raw if rules else None,
            "matched": None,
            "groups": None,
            "new_path": None,
            "status": 508,
            "location": None,
            "flow": "检测到循环",
            "note": (
                f"路径 {path_value} 再次回到规则 #{idx + 1}，"
                "状态重复，判定为改写循环并立即停止"
            ),
        }

    while True:
        if steps >= max_steps:
            finish_reason = "step_limit"
            status_code = 509
            final_location = current
            trace.append(
                {
                    "seq": steps + 1,
                    "type": "limit",
                    "path": current,
                    "rule_index": None,
                    "line": None,
                    "rule": None,
                    "matched": None,
                    "groups": None,
                    "new_path": None,
                    "status": 509,
                    "location": None,
                    "flow": "超过步数上限",
                    "note": f"已执行 {steps} 步仍未结束（上限 {max_steps} 步），强制停止",
                }
            )
            break

        if rule_index >= len(rules):
            finish_reason = "fallthrough"
            status_code = 200
            final_location = current
            trace.append(
                {
                    "seq": steps + 1,
                    "type": "end",
                    "path": current,
                    "rule_index": None,
                    "line": None,
                    "rule": None,
                    "matched": None,
                    "groups": None,
                    "new_path": None,
                    "status": 200,
                    "location": None,
                    "flow": "规则链结束",
                    "note": "所有规则均未终止请求，按当前路径内部放行（200）",
                }
            )
            break

        rule = rules[rule_index]
        steps += 1
        match = rule.pattern.search(current)

        if match is None:
            trace.append(
                {
                    "seq": steps,
                    "type": "test",
                    "path": current,
                    "rule_index": rule_index,
                    "line": rule.line,
                    "rule": rule.raw,
                    "matched": False,
                    "groups": None,
                    "new_path": None,
                    "status": None,
                    "location": None,
                    "flow": "未匹配 → 下一条",
                    "note": f"规则 #{rule_index + 1} 的正则未命中当前路径，继续尝试下一条",
                }
            )
            rule_index += 1
            continue

        groups = _group_entries(match)

        # 命中点循环检测：同一规则对同一路径二次命中，结果必然重复
        hit_state = (current, rule_index)
        if hit_state in seen_eval:
            finish_reason = "loop"
            status_code = 508
            final_location = current
            trace.append(_loop_trace(steps + 1, current, rule_index))
            break
        seen_eval.add(hit_state)

        if rule.kind == "rewrite":
            # REWRITE 与 nginx/Apache 一致：对【整个当前路径】做替换，
            # 而不是仅替换命中的子串（未被正则捕获的部分不会自动保留，
            # 需要时用 $0 / 捕获组带上）。
            new_path = expand(rule.pieces, match)
            restart = rule.flag == "last"
            trace.append(
                {
                    "seq": steps,
                    "type": "rewrite",
                    "path": current,
                    "rule_index": rule_index,
                    "line": rule.line,
                    "rule": rule.raw,
                    "matched": True,
                    "groups": groups,
                    "new_path": new_path,
                    "status": None,
                    "location": None,
                    "flow": "last：从头重新匹配" if restart else "next：继续下一条",
                    "note": (
                        f"规则 #{rule_index + 1} 命中，内部改写为 {new_path}"
                        + ("，并从第 1 条规则重新开始" if restart else "")
                    ),
                }
            )
            current = new_path
            rule_index = 0 if restart else rule_index + 1
            continue

        # redirect：规则链终止（Location 取替换串本身，允许子串命中替换）
        new_path = expand(rule.pieces, match)
        location_header = new_path
        internal = _is_internal_location(new_path)
        if follow_redirects and internal:
            if hops >= MAX_HOPS:
                finish_reason = "hop_limit"
                status_code = rule.status
                final_location = new_path
                trace.append(
                    {
                        "seq": steps,
                        "type": "redirect",
                        "path": current,
                        "rule_index": rule_index,
                        "line": rule.line,
                        "rule": rule.raw,
                        "matched": True,
                        "groups": groups,
                        "new_path": None,
                        "status": rule.status,
                        "location": new_path,
                        "flow": f"{rule.status} 重定向",
                        "note": (
                            f"命中重定向 → {new_path}，但已跟随 {MAX_HOPS} 跳，"
                            "达到重定向上限，停止模拟"
                        ),
                    }
                )
                break

            hops += 1
            trace.append(
                {
                    "seq": steps,
                    "type": "redirect",
                    "path": current,
                    "rule_index": rule_index,
                    "line": rule.line,
                    "rule": rule.raw,
                    "matched": True,
                    "groups": groups,
                    "new_path": None,
                    "status": rule.status,
                    "location": new_path,
                    "flow": f"{rule.status} 重定向 → 跟随",
                    "note": (
                        f"命中重定向 → {new_path}；浏览器发起新请求，"
                        f"从第 1 条规则重新匹配（第 {hops} 跳）"
                    ),
                }
            )
            current = new_path
            rule_index = 0
            continue

        # 不跟随，或外部地址（http(s)://、//host）：直接作为最终响应
        finish_reason = "redirect"
        status_code = rule.status
        final_location = new_path
        if not internal and follow_redirects:
            note = (
                f"命中重定向 → {new_path}（站外地址，不继续模拟），"
                f"返回 {rule.status} 与 Location"
            )
        elif follow_redirects:
            note = f"命中重定向 → {new_path}，返回 {rule.status} 与 Location"
        else:
            note = (
                f"命中重定向 → {new_path}，规则链终止，"
                f"返回 {rule.status} 与 Location（未跟随重定向）"
            )
        trace.append(
            {
                "seq": steps,
                "type": "redirect",
                "path": current,
                "rule_index": rule_index,
                "line": rule.line,
                "rule": rule.raw,
                "matched": True,
                "groups": groups,
                "new_path": None,
                "status": rule.status,
                "location": new_path,
                "flow": f"{rule.status} 重定向 · 终止",
                "note": note,
            }
        )
        break

    summary = {
        "request_path": path,
        "final_url": final_location,
        "status": status_code,
        "location": location_header,
        "finish_reason": finish_reason,
        "steps": steps,
        "hops": hops,
        "internal": _is_internal_location(final_location) if final_location else True,
    }
    return {"trace": trace, "result": summary}
