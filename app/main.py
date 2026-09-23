"""FastAPI 入口：调试 API + 静态页面。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .engine import parse_rules
from .examples import EXAMPLES
from .minimizer import MinimizeValidationError, MAX_PATHS, minimize_rules
from .simulator import simulate

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="URL 重写规则调试台",
    description="逐步模拟路径匹配、捕获组替换、重定向与内部改写规则链。",
    version="1.0.0",
)


class DebugRequest(BaseModel):
    rules: str = Field("", description="规则源文本")
    path: str = Field("/", description="待调试的请求路径")
    max_steps: int = Field(100, ge=1, le=500, description="步数上限")
    follow_redirects: bool = Field(False, description="是否跟随站内重定向继续模拟")

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请求路径不能为空")
        if not value.startswith("/"):
            raise ValueError("请求路径必须以 / 开头（例如 /users/42）")
        if any(ch in value for ch in (" ", "\n", "\r", "\t")):
            raise ValueError("请求路径不能包含空白字符")
        return value


class DebugResponse(BaseModel):
    ok: bool
    errors: list[dict] = []
    trace: list[dict] = []
    result: dict | None = None
    rule_count: int = 0


class MinimizeRequest(BaseModel):
    rules: str = Field("", description="规则源文本")
    paths: list[str] = Field(..., description="多条待保障的请求路径（每行一条）")
    max_steps: int = Field(100, ge=1, le=500, description="步数上限")
    follow_redirects: bool = Field(False, description="是否跟随站内重定向继续模拟")

    @field_validator("paths")
    @classmethod
    def _check_paths(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("请至少输入一条待保障的请求路径")
        if len(value) > MAX_PATHS:
            raise ValueError(f"待保障路径最多 {MAX_PATHS} 条")
        for idx, raw in enumerate(value, start=1):
            if not isinstance(raw, str):
                raise ValueError(f"第 {idx} 条路径不是文本")
            path = raw.strip()
            if not path:
                raise ValueError(f"第 {idx} 条请求路径为空")
            if not path.startswith("/"):
                raise ValueError(f"第 {idx} 条请求路径必须以 / 开头：{path}")
            if any(ch in path for ch in (" ", "\n", "\r", "\t")):
                raise ValueError(f"第 {idx} 条请求路径不能包含空白字符：{path}")
        return value


class PathOutcome(BaseModel):
    path: str
    before: dict
    after: dict
    unchanged: bool


class MinimizeResponse(BaseModel):
    ok: bool
    errors: list[dict] = []
    paths: list[str] = []
    removed_lines: list[int] = []
    minimized_rules: str = ""
    original_rule_count: int = 0
    minimized_rule_count: int = 0
    attempts: int = 0
    results: list[PathOutcome] = []


@app.get("/api/examples")
def get_examples() -> list[dict]:
    return EXAMPLES


@app.post("/api/debug", response_model=DebugResponse)
def debug(req: DebugRequest) -> DebugResponse:
    rules, errors = parse_rules(req.rules)

    if errors:
        return DebugResponse(
            ok=False,
            errors=[
                {
                    "line": e.line,
                    "col": e.col,
                    "length": e.length,
                    "message": e.message,
                    "source": e.source,
                }
                for e in errors
            ],
            rule_count=len(rules),
        )

    simulation = simulate(
        rules,
        req.path,
        max_steps=req.max_steps,
        follow_redirects=req.follow_redirects,
    )
    return DebugResponse(
        ok=True,
        trace=simulation["trace"],
        result=simulation["result"],
        rule_count=len(rules),
    )


@app.post("/api/minimize", response_model=MinimizeResponse)
def minimize(req: MinimizeRequest) -> MinimizeResponse:
    try:
        outcome = minimize_rules(
            req.rules,
            req.paths,
            max_steps=req.max_steps,
            follow_redirects=req.follow_redirects,
        )
    except MinimizeValidationError as exc:
        # 请求输入不合法（而非规则语法错误）：明确报错，由前端提示且不覆盖编辑器
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not outcome["ok"]:
        return MinimizeResponse(ok=False, errors=outcome["errors"])

    return MinimizeResponse(
        ok=True,
        paths=outcome["paths"],
        removed_lines=outcome["removed_lines"],
        minimized_rules=outcome["minimized_rules"],
        original_rule_count=outcome["original_rule_count"],
        minimized_rule_count=outcome["minimized_rule_count"],
        attempts=outcome["attempts"],
        results=outcome["results"],
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# 静态资源（原生 JS/CSS）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
