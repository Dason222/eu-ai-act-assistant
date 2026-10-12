"""REST API (FastAPI). Run: ``aiact-qa serve`` or ``uvicorn aiact_qa.api:app``. Docs at /docs."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aiact_qa import __version__
from aiact_qa.budget import BudgetExceeded
from aiact_qa.config import Settings, get_settings

log = logging.getLogger(__name__)


def client_identity(headers: Any, peer: str = "") -> str:
    """Per-client quota key. Traefik drops client-sent X-Forwarded-* headers from untrusted peers and
    sets X-Real-Ip itself, so behind it this is the visitor's address; the peer address otherwise.
    Global quotas in the SQLite budget always apply, whatever the identity."""
    real_ip = (headers.get("x-real-ip") or "").strip()
    return "ip-" + (real_ip or peer or "unknown")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    top_k: int | None = Field(default=None, ge=1, le=20)
    provider: str | None = Field(default=None, description="anthropic, openai, azure_openai, mistral, ...")
    model: str | None = Field(default=None, max_length=100)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)


class FeedbackRequest(BaseModel):
    question: str
    answer: str
    rating: Literal["up", "down"]
    comment: str | None = None
    sources: list[str] = Field(default_factory=list)
    provider: str | None = None


def create_app(engine: Any = None, settings: Settings | None = None) -> FastAPI:
    """App factory; tests inject a fake engine and settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "engine", None) is None:
            from aiact_qa.pipeline import QAEngine

            app.state.engine = QAEngine.from_settings(app.state.settings)
        yield

    app = FastAPI(title="EU AI Act Q&A API", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings or get_settings()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def get_state(request: Request) -> tuple[Any, Settings]:
        return request.app.state.engine, request.app.state.settings

    def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
        expected = request.app.state.settings.api_key
        if expected and x_api_key != expected:
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")

    def resolve(request: Request, body: AskRequest) -> tuple[Any, list[dict[str, str]], str]:
        engine, settings = get_state(request)
        if len(body.question) > settings.max_question_chars:
            raise HTTPException(422, f"question longer than {settings.max_question_chars} characters")
        try:
            engine = engine.for_provider(body.provider, body.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        history = [t.model_dump() for t in body.history][-2 * settings.max_history_turns :]
        identity = client_identity(request.headers, request.client.host if request.client else "")
        return engine, history, identity

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        engine, _ = get_state(request)
        return {"status": "ok", "version": __version__, **engine.stats()}

    @app.get("/providers", dependencies=[Depends(require_key)])
    def providers(request: Request) -> dict[str, Any]:
        engine, _ = get_state(request)
        return {"providers": engine.available_providers()}

    @app.post("/ask", dependencies=[Depends(require_key)])
    def ask(request: Request, body: AskRequest) -> dict[str, Any]:
        engine, history, identity = resolve(request, body)
        try:
            return engine.ask(body.question, history=history, top_k=body.top_k, identity=identity).to_dict()
        except BudgetExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            log.exception("ask failed")
            raise HTTPException(500, "internal error while answering") from exc

    @app.post("/ask/stream", dependencies=[Depends(require_key)])
    def ask_stream(request: Request, body: AskRequest) -> StreamingResponse:
        engine, history, identity = resolve(request, body)

        def events() -> Iterator[str]:
            try:
                for event in engine.ask_stream(
                    body.question, history=history, top_k=body.top_k, identity=identity
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except BudgetExceeded:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Daily request or budget limit reached'})}\n\n"
            except Exception:
                log.exception("stream failed")
                yield f"data: {json.dumps({'type': 'error', 'message': 'The request could not be completed'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/search", dependencies=[Depends(require_key)])
    def search(request: Request, body: SearchRequest) -> dict[str, Any]:
        engine, _ = get_state(request)
        return {"results": [s.to_dict() for s in engine.search(body.query, k=body.k)]}

    @app.get("/provisions/{provision_id}", dependencies=[Depends(require_key)])
    def provision(request: Request, provision_id: str) -> dict[str, Any]:
        engine, _ = get_state(request)
        found = engine.get_provision(provision_id)
        if found is None:
            raise HTTPException(404, f"unknown provision '{provision_id}' (use ids like art_5, rct_27, anx_III)")
        return {"label": found.label, **found.__dict__, "paragraphs": [p.__dict__ for p in found.paragraphs]}

    @app.post("/feedback", dependencies=[Depends(require_key)])
    def feedback(request: Request, body: FeedbackRequest) -> dict[str, str]:
        _, settings = get_state(request)
        record_feedback(settings, body.model_dump())
        return {"status": "recorded"}

    return app


def record_feedback(settings: Settings, record: dict[str, Any]) -> None:
    path = settings.feedback_path
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(UTC).isoformat(), **record}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


app = create_app()
