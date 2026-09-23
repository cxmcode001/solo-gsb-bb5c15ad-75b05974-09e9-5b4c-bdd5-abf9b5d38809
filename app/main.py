"""FastAPI 入口：调试 API + 静态页面。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .engine import parse_rules
from .examples import EXAMPLES
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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# 静态资源（原生 JS/CSS）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
