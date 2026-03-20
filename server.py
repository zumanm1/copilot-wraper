from __future__ import annotations
import asyncio, time, uuid, base64, tempfile, os
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from models import (
    # Chat completion models
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChoice,
    ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionChunkDelta,
    ChatMessage, UsageInfo, ModelList, ModelInfo,
    # Agent management models
    AgentStartRequest, AgentStartResponse,
    AgentStopResponse, AgentPauseResponse, AgentResumeResponse,
    AgentTaskRequest, AgentTaskResponse,
    AgentStatusResponse, AgentHistoryResponse, AgentClearHistoryResponse,
)
from copilot_backend import CopilotBackend, get_connection_pool, get_cache_stats
from agent_manager import get_agent_manager
import config

# ── Fast JSON serialization ───────────────────────────────────────────────────
# orjson is 2-3× faster than stdlib json for small SSE payloads.
# Falls back to stdlib so local dev without orjson still works.
try:
    import orjson as _orjson
    def _dumps(obj: dict) -> str:
        return _orjson.dumps(obj).decode()
except ImportError:
    import json as _json_fallback
    def _dumps(obj: dict) -> str:
        return _json_fallback.dumps(obj)


app = FastAPI(title="Copilot OpenAI-Compatible API", version="1.0.0")


# ══════════════════════════════════════════════════════════════════════
# Startup / Shutdown lifecycle
# ══════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Pre-warm the connection pool so the first real request is fast."""
    pool = get_connection_pool()
    warm_tasks = [_warm_one(pool) for _ in range(config.POOL_WARM_COUNT)]
    await asyncio.gather(*warm_tasks, return_exceptions=True)


async def _warm_one(pool):
    try:
        b = CopilotBackend()
        await b._get_client()
        await pool.release(b)
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown_event():
    from copilot_backend import close_connection_pool
    await close_connection_pool()


# ══════════════════════════════════════════════════════════════════════
# Utility helpers
# ══════════════════════════════════════════════════════════════════════

def extract_user_prompt(messages):
    parts = []
    for msg in messages:
        if msg.role == "system":
            parts.append(f"[System]: {msg.content}")
        elif msg.role == "user":
            if isinstance(msg.content, str):
                parts.append(msg.content)
            elif isinstance(msg.content, list):
                for p in msg.content:
                    if p.type == "text" and p.text:
                        parts.append(p.text)
    return parts[-1] if parts else ""


def extract_image(messages):
    """Return path to a temp file containing the first base64 image, or None."""
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            for p in msg.content:
                if p.type == "image_url" and p.image_url:
                    url = p.image_url.url
                    if url.startswith("data:"):
                        _, data = url.split(",", 1)
                        fd, path = tempfile.mkstemp(suffix=".png")
                        with os.fdopen(fd, "wb") as f:
                            f.write(base64.b64decode(data))
                        return path
    return None


def _cleanup_attachment(path):
    """Delete a temp attachment file, ignoring errors."""
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# Core API Endpoints
# ══════════════════════════════════════════════════════════════════════

@app.get("/v1/models")
async def list_models():
    ts = int(time.time())
    models = [
        ModelInfo(id="copilot",          object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="gpt-4",            object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="gpt-4o",           object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-balanced", object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-creative", object="model", created=ts, owned_by="microsoft"),
        ModelInfo(id="copilot-precise",  object="model", created=ts, owned_by="microsoft"),
    ]
    return ModelList(object="list", data=models)


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    prompt = extract_user_prompt(request.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="No user message found")

    attachment = extract_image(request.messages)
    pool = get_connection_pool()

    # ── Streaming path ────────────────────────────────────────────────
    if request.stream:
        backend = await pool.acquire()
        return StreamingResponse(
            stream_gen(pool, backend, prompt, attachment, request.model),
            media_type="text/event-stream",
        )

    # ── Non-streaming path ────────────────────────────────────────────
    backend = await pool.acquire()
    try:
        response = await backend.chat_completion(prompt=prompt, attachment_path=attachment)
        return ChatCompletionResponse(
            model=request.model,
            choices=[ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response),
                finish_reason="stop",
            )],
            usage=UsageInfo(
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(response.split()),
                total_tokens=len(prompt.split()) + len(response.split()),
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await pool.release(backend)
        _cleanup_attachment(attachment)


async def stream_gen(pool, backend, prompt, attachment, model):
    """SSE generator — owns the pool backend and temp file for the lifetime of the stream."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())   # computed once, not per-token
    try:
        init = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        yield f"data: {_dumps(init)}\n\n"

        async for token in backend.chat_completion_stream(prompt=prompt, attachment_path=attachment):
            chunk = {
                "id": chat_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
            }
            yield f"data: {_dumps(chunk)}\n\n"

        end = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {_dumps(end)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {_dumps({'error': {'message': str(e), 'type': 'server_error'}})}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        await pool.release(backend)
        _cleanup_attachment(attachment)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "copilot-openai-wrapper"}


@app.get("/v1/cache/stats")
async def cache_stats():
    """Returns response-cache hit/miss counters."""
    return get_cache_stats()


# ══════════════════════════════════════════════════════════════════════
# Agent Management Endpoints
# ══════════════════════════════════════════════════════════════════════

@app.post(
    "/v1/agent/start",
    response_model=AgentStartResponse,
    summary="Start the AI agent",
    description=(
        "Initialises a new agent session backed by a persistent Microsoft Copilot "
        "WebSocket connection. The agent maintains full conversation context across "
        "all subsequent /v1/agent/task calls until stopped."
    ),
    tags=["Agent"],
)
async def agent_start(request: AgentStartRequest = AgentStartRequest()):
    manager = get_agent_manager()
    try:
        result = await manager.start(system_prompt=request.system_prompt)
        return AgentStartResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/stop",
    response_model=AgentStopResponse,
    summary="Stop the AI agent",
    description=(
        "Gracefully stops the agent, closes the Copilot WebSocket connection, "
        "and returns a summary of tasks completed in the session."
    ),
    tags=["Agent"],
)
async def agent_stop():
    manager = get_agent_manager()
    try:
        result = await manager.stop()
        return AgentStopResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/pause",
    response_model=AgentPauseResponse,
    summary="Pause the AI agent",
    description=(
        "Pauses the agent. While paused, task submissions are rejected with 409. "
        "The Copilot session remains open. Call /v1/agent/resume to continue."
    ),
    tags=["Agent"],
)
async def agent_pause():
    manager = get_agent_manager()
    try:
        result = await manager.pause()
        return AgentPauseResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/resume",
    response_model=AgentResumeResponse,
    summary="Resume the AI agent",
    description="Resumes a paused agent so it can accept new tasks again.",
    tags=["Agent"],
)
async def agent_resume():
    manager = get_agent_manager()
    try:
        result = await manager.resume()
        return AgentResumeResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/v1/agent/task",
    summary="Give the agent a task",
    description=(
        "Submit a task or question to the running agent. "
        "Set `stream: true` to receive a streaming SSE response. "
        "Example: `{\"task\": \"What is 1 + 1?\"}` "
        "The agent maintains full conversation history across tasks in the same session."
    ),
    tags=["Agent"],
)
async def agent_task(request: AgentTaskRequest):
    manager = get_agent_manager()

    # ── Streaming response ────────────────────────────────────────────
    if request.stream:
        async def sse_stream():
            task_id  = f"task-{uuid.uuid4().hex[:12]}"
            chat_id  = f"agentcmpl-{uuid.uuid4().hex[:20]}"
            created  = int(time.time())
            session  = manager.session_id

            init = {
                "id": chat_id, "task_id": task_id,
                "session_id": session,
                "object": "agent.task.chunk", "created": created,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {_dumps(init)}\n\n"

            try:
                async for token in manager.run_task_stream(request.task):
                    chunk = {
                        "id": chat_id, "task_id": task_id,
                        "object": "agent.task.chunk", "created": created,
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                    }
                    yield f"data: {_dumps(chunk)}\n\n"

                end = {
                    "id": chat_id, "task_id": task_id,
                    "object": "agent.task.chunk", "created": created,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {_dumps(end)}\n\n"
                yield "data: [DONE]\n\n"

            except ValueError as exc:
                yield f"data: {_dumps({'error': {'message': str(exc), 'type': 'agent_error'}})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f"data: {_dumps({'error': {'message': str(exc), 'type': 'server_error'}})}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(sse_stream(), media_type="text/event-stream")

    # ── Non-streaming response ────────────────────────────────────────
    try:
        task = await manager.run_task(request.task)
        return AgentTaskResponse(
            task_id      = task.task_id,
            session_id   = manager.session_id,
            status       = task.status.value,
            prompt       = task.prompt,
            result       = task.result,
            error        = task.error,
            created_at   = task.created_at.isoformat(),
            completed_at = task.completed_at.isoformat() if task.completed_at else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    "/v1/agent/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Returns the current status of the agent and task statistics.",
    tags=["Agent"],
)
async def agent_status():
    manager = get_agent_manager()
    return AgentStatusResponse(**manager.get_status())


@app.get(
    "/v1/agent/history",
    response_model=AgentHistoryResponse,
    summary="Get agent task history",
    description="Returns the full list of tasks submitted in the current session.",
    tags=["Agent"],
)
async def agent_history():
    manager = get_agent_manager()
    tasks = manager.get_history()
    return AgentHistoryResponse(
        session_id=manager.session_id,
        tasks=tasks,
        total=len(tasks),
    )


@app.get(
    "/v1/agent/history/{task_id}",
    summary="Get a specific task by ID",
    description="Returns the details of a single task by its task_id.",
    tags=["Agent"],
)
async def agent_get_task(task_id: str):
    manager = get_agent_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


@app.delete(
    "/v1/agent/history",
    response_model=AgentClearHistoryResponse,
    summary="Clear agent task history",
    description="Clears all task history for the current session (does not stop the agent).",
    tags=["Agent"],
)
async def agent_clear_history():
    manager = get_agent_manager()
    result = manager.clear_history()
    return AgentClearHistoryResponse(**result)


if __name__ == "__main__":
    import uvicorn
    config.validate_config()
    uvicorn.run("server:app", host=config.HOST, port=config.PORT, reload=config.RELOAD)
