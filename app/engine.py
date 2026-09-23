"""URL 重写规则引擎：行式规则解析 + 顺序链模拟。

规则语法（每行一条，# 起始为注释）：

    REWRITE  <正则>  <替换串>  [next|last]
    REDIRECT <正则>  <替换串>  <3xx状态码>

- REWRITE 默认 next（替换后继续下一条），last 表示从头重新匹配。
- REDIRECT 匹配即终止，给出 3xx 响应与 Location。
- 替换串支持 $0..$n、${n}、${name}；引号内支持 $/$、$n 等。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass
class RuleError:
    """一条规则的语法错误定位信息（行列均从 1 开始）。"""

    line: int
    col: int
    length: int
    message: str
    source: str = ""


@dataclass
class Token:
    value: str
    col: int  # 1 起始，引号 token 的 col 指向开引号
    end_col: int  # 1 起始，指向 token 最后一个字符的后一位
    quoted: bool
    srcmap: list[int] = field(default_factory=list)
    # srcmap[j]：反转义后字符串的第 j 个字符，对应在原行（1 起始列）的列号
    # srcmap[j] == -1 表示该字符由转义序列产生，定位到转义符起始


@dataclass
class Rule:
    kind: str  # "rewrite" | "redirect"
    pattern: re.Pattern
    pattern_src: str
    pieces: list  # [str | int]：字面量 / 组号，用于自实现展开
    replacement: str  # 原样替换串（回显用）
    raw: str
    line: int
    flag: str = "next"  # rewrite: next|last
    status: int = 302  # redirect
    groups: dict = field(default_factory=dict)  # 命名组 -> 组号


# ---------------------------------------------------------------------------
# 词法切分（带列定位与双引号支持）
# ---------------------------------------------------------------------------


def tokenize(line: str) -> tuple[list[Token], list[tuple[int, str]]]:
    """切分一行文本。

    返回 (tokens, 词法错误)，错误为 (col, message)，列从 1 开始。
    """
    tokens: list[Token] = []
    errors: list[tuple[int, str]] = []
    i, n = 0, len(line)

    while i < n:
        if line[i].isspace():
            i += 1
            continue

        start = i  # 0 起始下标

        if line[i] == '"':
            chars: list[str] = []
            srcmap: list[int] = []
            i += 1
            closed = False
            while i < n:
                ch = line[i]
                if ch == '"':
                    i += 1  # 跨过闭引号
                    closed = True
                    break
                if ch == "\\":
                    esc_col = i + 1
                    if i + 1 >= n:
                        errors.append((esc_col, "转义符 \\ 后缺少字符"))
                        i = n
                        break
                    nxt = line[i + 1]
                    mapping = {
                        '"': '"',
                        "\\": "\\",
                        "n": "\n",
                        "t": "\t",
                    }
                    if nxt in mapping:
                        chars.append(mapping[nxt])
                        srcmap.append(esc_col)  # 定位到反斜杠
                        i += 2
                    else:
                        errors.append(
                            (i + 2, f"非法转义序列 \\{nxt}（仅支持 \\\" \\\\ \\n \\t）")
                        )
                        i += 2
                else:
                    chars.append(ch)
                    srcmap.append(i + 1)
                    i += 1

            if not closed:
                errors.append((start + 1, "字符串缺少闭合的双引号"))
            tokens.append(
                Token(
                    value="".join(chars),
                    col=start + 1,
                    end_col=i + 1,
                    quoted=True,
                    srcmap=srcmap,
                )
            )
        else:
            j = i
            while j < n and not line[j].isspace() and line[j] != '"':
                j += 1
            value = line[i:j]
            tokens.append(
                Token(
                    value=value,
                    col=i + 1,
                    end_col=j + 1,
                    quoted=False,
                    srcmap=list(range(i + 1, j + 1)),
                )
            )
            i = j

    return tokens, errors


def strip_comment(line: str) -> str:
    """去掉行尾注释：# 位于行首空白后或空白之后且不在引号内时生效。"""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote:
            if i == 0 or line[i - 1].isspace():
                return line[:i]
    return line


# ---------------------------------------------------------------------------
# 替换串：$ 引用校验与展开
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"\$\{(?P<braced>[^}]*)\}|\$(?P<simple>[0-9]+)")


def scan_replacement(
    text: str,
    srcmap: list[int],
    named_groups: dict[str, int],
    group_count: int,
) -> tuple[list, list[tuple[int, int, str]]]:
    """把替换串解析为展开片段，并校验所有组引用是否存在。

    named_groups：命名组 -> 组号；group_count：数字捕获组总数（不含整匹配）。
    返回 (pieces, errors)；pieces 元素为 str（字面量）或 int（组号）。
    errors 元素为 (col, length, message)，列 1 起始。
    """
    pieces: list = []
    errors: list[tuple[int, int, str]] = []
    literals: list[str] = []

    def flush() -> None:
        if literals:
            pieces.append("".join(literals))
            literals.clear()

    pos = 0
    total = len(text)
    while pos < total:
        ch = text[pos]
        if ch == "$":
            dollar_col = srcmap[pos] if pos < len(srcmap) else (pos + 1)
            nxt = text[pos + 1] if pos + 1 < total else ""

            if nxt == "$":
                literals.append("$")
                pos += 2
                continue
            if nxt == "":
                errors.append((dollar_col, 1, "替换串以 $ 结尾，缺少组引用（写 $$ 表示字面量 $）"))
                pos += 1
                continue

            m = _REF_RE.match(text, pos)
            if not m or m.start() != pos:
                # $ 后既不是数字也不是 {数字/名字}
                errors.append(
                    (
                        dollar_col,
                        1,
                        f"无效的替换引用 ${nxt}：应为 $数字、${{数字}}、${{命名}} 或 $$",
                    )
                )
                pos += 1
                continue

            braced = m.group("braced")
            simple = m.group("simple")
            ref_end = m.end()
            last_col = (
                srcmap[ref_end - 1] if ref_end - 1 < len(srcmap) else dollar_col
            )
            length = max(1, last_col - dollar_col + 1)

            if braced is not None:
                name = braced
                if name == "":
                    errors.append((dollar_col, length, "${} 中缺少组号或命名组"))
                    pos = ref_end
                    continue
                if name.isdigit():
                    num = int(name)
                    if num > group_count:
                        errors.append(
                            (
                                dollar_col,
                                length,
                                f"正则中不存在第 {num} 个捕获组"
                                f"（共 {group_count} 个）",
                            )
                        )
                    else:
                        flush()
                        pieces.append(num)
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    if name not in named_groups:
                        errors.append(
                            (
                                dollar_col,
                                length,
                                f"正则中不存在命名捕获组 '{name}'",
                            )
                        )
                    else:
                        flush()
                        pieces.append(named_groups[name])
                else:
                    errors.append(
                        (
                            dollar_col,
                            length,
                            f"非法的捕获组引用 '${{{name}}}'：应为数字或合法标识符",
                        )
                    )
                pos = ref_end
            else:
                num = int(simple)
                if num > group_count:
                    errors.append(
                        (
                            dollar_col,
                            length,
                            f"正则中不存在第 {num} 个捕获组"
                            f"（共 {group_count} 个）",
                        )
                    )
                else:
                    flush()
                    pieces.append(num)
                pos = ref_end
        else:
            literals.append(ch)
            pos += 1

    flush()
    return pieces, errors


def expand(pieces: list, match: re.Match | None) -> str:
    if match is None:
        return "".join(p for p in pieces if isinstance(p, str))
    out: list[str] = []
    for piece in pieces:
        if isinstance(piece, int):
            out.append(match.group(piece) or "")
        else:
            out.append(piece)
    return "".join(out)


# ---------------------------------------------------------------------------
# 规则解析
# ---------------------------------------------------------------------------

_VALID_STATUSES = {301, 302, 303, 307, 308}


def _named_groups(pattern: re.Pattern) -> dict[str, int]:
    return {name: int(idx) for name, idx in pattern.groupindex.items()}


def parse_rules(source: str) -> tuple[list[Rule], list[RuleError]]:
    rules: list[Rule] = []
    errors: list[RuleError] = []

    for lineno, raw_line in enumerate(source.splitlines(), start=1):
        stripped = strip_comment(raw_line).strip()
        if not stripped:
            continue

        tokens, lex_errors = tokenize(stripped)
        for col, message in lex_errors:
            errors.append(
                RuleError(
                    line=lineno, col=col, length=1, message=message, source=stripped
                )
            )
        if lex_errors:
            continue
        if not tokens:
            continue

        action_tok = tokens[0]
        action = action_tok.value.upper()

        if action not in ("REWRITE", "REDIRECT"):
            errors.append(
                RuleError(
                    line=lineno,
                    col=action_tok.col,
                    length=max(1, action_tok.end_col - action_tok.col),
                    message=f"未知动作 '{action_tok.value}'，应为 REWRITE 或 REDIRECT",
                    source=stripped,
                )
            )
            continue

        # REWRITE:  <pattern> <replacement> [next|last]   （标志可省略，默认 next）
        # REDIRECT: <pattern> <replacement> <status>
        min_tokens = 3 if action == "REWRITE" else 4
        if len(tokens) < min_tokens:
            missing = "状态码（301/302/303/307/308）"
            errors.append(
                RuleError(
                    line=lineno,
                    col=len(stripped) + 1,
                    length=1,
                    message=f"{action} 缺少参数：{missing}",
                    source=stripped,
                )
            )
            continue
        if len(tokens) > 4:
            extra = tokens[4]
            errors.append(
                RuleError(
                    line=lineno,
                    col=extra.col,
                    length=max(1, extra.end_col - extra.col),
                    message=f"多余的参数 '{extra.value}'",
                    source=stripped,
                )
            )
            continue

        pattern_tok, repl_tok = tokens[1], tokens[2]

        try:
            compiled = re.compile(pattern_tok.value)
        except re.error as exc:
            pos = exc.pos
            col = (
                pattern_tok.srcmap[pos]
                if pos is not None and pos < len(pattern_tok.srcmap)
                else pattern_tok.col
            )
            if col == -1:
                col = pattern_tok.col
            errors.append(
                RuleError(
                    line=lineno,
                    col=col,
                    length=1,
                    message=f"正则表达式错误：{exc.msg}",
                    source=stripped,
                )
            )
            continue

        named = _named_groups(compiled)
        group_count = compiled.groups
        pieces, repl_errors = scan_replacement(
            repl_tok.value, repl_tok.srcmap, named, group_count
        )
        bad = False
        for col, length, message in repl_errors:
            errors.append(
                RuleError(
                    line=lineno, col=col, length=length, message=message, source=stripped
                )
            )
            bad = True
        if bad:
            continue

        if action == "REWRITE":
            if len(tokens) == 4:
                flag_tok = tokens[3]
                flag = flag_tok.value.lower()
                if flag not in ("next", "last"):
                    errors.append(
                        RuleError(
                            line=lineno,
                            col=flag_tok.col,
                            length=max(1, flag_tok.end_col - flag_tok.col),
                            message=f"未知执行标志 '{flag_tok.value}'，应为 next 或 last",
                            source=stripped,
                        )
                    )
                    continue
            else:
                flag = "next"
            rules.append(
                Rule(
                    kind="rewrite",
                    pattern=compiled,
                    pattern_src=pattern_tok.value,
                    pieces=pieces,
                    replacement=repl_tok.value,
                    raw=stripped,
                    line=lineno,
                    flag=flag,
                    groups=named,
                )
            )
        else:
            status_tok = tokens[3]
            status_value = status_tok.value
            if not re.fullmatch(r"[0-9]{3}", status_value):
                errors.append(
                    RuleError(
                        line=lineno,
                        col=status_tok.col,
                        length=max(1, status_tok.end_col - status_tok.col),
                        message=f"状态码 '{status_value}' 不是三位数字",
                        source=stripped,
                    )
                )
                continue
            status = int(status_value)
            if status not in _VALID_STATUSES:
                errors.append(
                    RuleError(
                        line=lineno,
                        col=status_tok.col,
                        length=max(1, status_tok.end_col - status_tok.col),
                        message=f"状态码 {status} 不受支持，可选：{sorted(_VALID_STATUSES)}",
                        source=stripped,
                    )
                )
                continue
            rules.append(
                Rule(
                    kind="redirect",
                    pattern=compiled,
                    pattern_src=pattern_tok.value,
                    pieces=pieces,
                    replacement=repl_tok.value,
                    raw=stripped,
                    line=lineno,
                    status=status,
                    groups=named,
                )
            )

    return rules, errors
