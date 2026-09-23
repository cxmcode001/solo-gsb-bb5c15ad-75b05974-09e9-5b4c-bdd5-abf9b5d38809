"""规则精简：在保持给定路径集合最终结果不变的前提下，贪心删除冗余规则行。

策略（确定性、1-minimal，不要求全局最短）：

1. 用原始规则集模拟全部待保障路径，记录每条路径的基线结果
   （最终状态码、最终 URL、Location、结束原因四元组）。
2. 按原行号升序反复扫描剩余的有效规则行：尝试把该行从源文本中
   整行移除，重新解析并模拟全部路径；若所有路径的结果四元组与
   基线完全一致，则接受这次删除，否则恢复该行。
3. 一整轮扫描没有任何删除时终止 —— 此时剩余任一规则被单独删除
   都会改变至少一条路径的结果。

只删除「解析出有效规则」的完整行；注释、空行与其余行保持原文与
相对顺序。为控制 O(规则数² × 路径数) 的尝试规模，设有模拟步数
总预算：预算耗尽时提前停止，已接受的删除均已验证，结果仍然有效，
只是可能尚未达到 1-minimal（响应中 completed=False 标识）。
"""

from __future__ import annotations

from .engine import parse_rules
from .simulator import simulate

MAX_MINIMIZE_RULES = 300  # 有效规则数上限，超出直接拒绝（避免尝试次数失控）
STEP_BUDGET = 5_000_000  # 全部尝试累计可执行的模拟步数预算


def result_key(result: dict) -> tuple:
    """参与等价性比较的最终结果字段：状态码 / 最终 URL / Location / 结束原因。"""
    return (
        result["status"],
        result["final_url"],
        result["location"],
        result["finish_reason"],
    )


class _Budget:
    """模拟步数预算：耗尽后停止进一步精简（已接受的删除仍然有效）。"""

    def __init__(self, total: int) -> None:
        self.remaining = total

    def spend(self, steps: int) -> None:
        self.remaining -= steps

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


def _matches_baseline(
    source: str,
    paths: list[str],
    baseline_keys: list[tuple],
    max_steps: int,
    follow_redirects: bool,
    budget: _Budget,
) -> bool:
    """trial 源文本下全部路径的结果四元组是否都与基线一致。

    逐条路径模拟，发现第一条不一致立即失败（早退），不再浪费预算。
    调用前提：source 由「删除有效规则行」得到，必然可以解析。
    """
    rules, errors = parse_rules(source)
    if errors:  # 防御：删除有效规则行不会引入新错误
        return False
    for path, want in zip(paths, baseline_keys):
        sim = simulate(
            rules, path, max_steps=max_steps, follow_redirects=follow_redirects
        )
        budget.spend(sim["result"]["steps"])
        if result_key(sim["result"]) != want:
            return False
    return True


def minimize_rules(
    source: str,
    paths: list[str],
    max_steps: int = 100,
    follow_redirects: bool = False,
) -> dict:
    """对 source 中的规则做 1-minimal 精简，返回精简结果字典。

    返回结构（ok=True 时）：
        minimized_rules   精简后的完整源文本（保留注释 / 空行 / 原文顺序）
        removed_lines     被删除的规则行号（相对原始文本，1 起始，升序）
        kept_lines        保留的规则行号（1 起始，升序）
        rule_count_before / rule_count_after
        rounds            实际执行的扫描轮数
        completed         True 表示已达到 1-minimal；False 表示预算耗尽提前停止
        paths             每条路径的精简前后结果对比
    ok=False 时返回 errors（语法错误定位，同 /api/debug）或 message（其它原因）。
    """
    rules, errors = parse_rules(source)
    if errors:
        return {
            "ok": False,
            "errors": [
                {
                    "line": e.line,
                    "col": e.col,
                    "length": e.length,
                    "message": e.message,
                    "source": e.source,
                }
                for e in errors
            ],
            "rule_count_before": len(rules),
        }

    if len(rules) > MAX_MINIMIZE_RULES:
        return {
            "ok": False,
            "message": (
                f"有效规则过多（{len(rules)} 条，上限 {MAX_MINIMIZE_RULES} 条），"
                "请分批精简或减少规则后重试"
            ),
            "rule_count_before": len(rules),
        }

    lines = source.splitlines()
    budget = _Budget(STEP_BUDGET)

    # 基线：原始规则集下每条路径的最终结果
    baseline_keys: list[tuple] = []
    baseline_results: list[dict] = []
    for path in paths:
        sim = simulate(
            rules, path, max_steps=max_steps, follow_redirects=follow_redirects
        )
        budget.spend(sim["result"]["steps"])
        baseline_keys.append(result_key(sim["result"]))
        baseline_results.append(sim["result"])

    def build_source(removed: set[int]) -> str:
        return "\n".join(
            line for lineno, line in enumerate(lines, start=1) if lineno not in removed
        )

    removed: set[int] = set()
    candidates = [r.line for r in rules]  # 原行号，解析顺序即升序
    rounds = 0
    completed = True

    # 按原行号升序反复扫描；每接受一次删除，候选集同步缩小。
    # 一整轮无删除 → 任一剩余规则单独删除都会改变结果，终止。
    changed = True
    while changed and candidates:
        changed = False
        rounds += 1
        for lineno in list(candidates):
            if budget.exhausted:
                completed = False
                break
            trial = removed | {lineno}
            if _matches_baseline(
                build_source(trial),
                paths,
                baseline_keys,
                max_steps,
                follow_redirects,
                budget,
            ):
                removed = trial
                candidates.remove(lineno)
                changed = True
        if budget.exhausted:
            completed = False
            break

    minimized = build_source(removed)

    # 用精简后的规则集重新模拟，收集各路径的 after 结果用于展示与最终校验
    final_rules, _ = parse_rules(minimized)
    path_reports = []
    for path, before, want in zip(paths, baseline_results, baseline_keys):
        sim = simulate(
            final_rules, path, max_steps=max_steps, follow_redirects=follow_redirects
        )
        after = sim["result"]
        path_reports.append(
            {
                "path": path,
                "before": before,
                "after": after,
                "unchanged": result_key(after) == want,
            }
        )

    return {
        "ok": True,
        "minimized_rules": minimized,
        "removed_lines": sorted(removed),
        "kept_lines": candidates,
        "rule_count_before": len(rules),
        "rule_count_after": len(candidates),
        "rounds": rounds,
        "completed": completed,
        "paths": path_reports,
    }
