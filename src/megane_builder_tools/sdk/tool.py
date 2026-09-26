"""The ``@builder_tool`` decorator: a typed Python function → a Builder button (§3–§6).

A decorated function takes its arguments as ordinary annotated parameters and
returns a :class:`BuilderResult`. The decorator derives what the contract
requires and the function should not have to repeat:

- the ``_meta["io.github.megane-labs/builder"]`` marker, with ``stochastic``
  and ``document`` read off the signature (a ``Seed`` parameter named
  ``seed``, a ``DocumentInput`` parameter);
- the ``outputSchema`` (``BuilderResult``) and the text summary MCP asks for
  next to ``structuredContent``;
- ``provenance`` defaults (the seed actually used and this package's version);
- running synchronous functions in a worker thread, so a long computation does
  not block progress notifications or cancellation.
"""

from __future__ import annotations

import inspect
import threading
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, Icon, TextContent, ToolAnnotations

from .contract import (
    APPLY_MODES,
    CATEGORIES,
    CONTRACT_VERSION,
    META_KEY,
    WIDGET_KEY,
    ApplyMode,
    Category,
    DocumentUse,
)
from .models import BuilderResult

__all__ = ["BuilderTool", "CallLimits", "Progress", "ToolError", "builder_tool", "widget_of"]


class CallLimits:
    """Server-wide limits on tool calls: a time budget per call and a number of concurrent calls.

    A call that waits for a free slot or runs past ``timeout`` seconds fails with a
    ``ToolError``, so a client behind a proxy with a hard request timeout (App
    Runner closes requests after 120 s) gets a readable error instead of a
    dropped connection. A synchronous tool cannot be interrupted, so its slot is
    released by its worker thread when the computation really ends, not when the
    call gives up: an abandoned computation still counts against the limit.
    """

    def __init__(self, timeout: float | None = None, max_concurrency: int | None = None) -> None:
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.timeout = timeout
        self.max_concurrency = max_concurrency
        self._slots = threading.BoundedSemaphore(max_concurrency) if max_concurrency else None

    async def acquire(self) -> None:
        if self._slots is None:
            return
        while not self._slots.acquire(blocking=False):
            await anyio.sleep(0.05)

    def release(self) -> None:
        if self._slots is not None:
            self._slots.release()


NO_LIMITS = CallLimits()


class Progress:
    """Progress reporting handed to a tool that declares a ``Progress`` parameter.

    Synchronous tools (run in a worker thread) call ``progress(0.4, "packing")``;
    async tools ``await progress.areport(0.4, "packing")``. Fractions are 0..1.
    Without a client progress token both are no-ops.
    """

    def __init__(self, ctx: Context | None = None) -> None:
        self._ctx = ctx
        self.reports: list[tuple[float, str | None]] = []

    async def areport(self, fraction: float, message: str | None = None) -> None:
        fraction = min(max(float(fraction), 0.0), 1.0)
        self.reports.append((fraction, message))
        if self._ctx is not None:
            await self._ctx.report_progress(fraction, 1.0, message)

    def __call__(self, fraction: float, message: str | None = None) -> None:
        if self._ctx is None:
            self.reports.append((min(max(float(fraction), 0.0), 1.0), message))
            return
        anyio.from_thread.run(self.areport, fraction, message)


def widget_of(annotation: Any) -> str | None:
    """The ``x-megane-widget`` an ``Annotated`` parameter type carries, if any."""
    for meta in getattr(annotation, "__metadata__", ()):
        extra = getattr(meta, "json_schema_extra", None)
        if isinstance(extra, dict) and WIDGET_KEY in extra:
            return str(extra[WIDGET_KEY])
    return None


def _cell_lengths(result: BuilderResult) -> str:
    cell = result.structure.cell_matrix()
    if cell is None:
        return "no cell"
    a, b, c = (float((row**2).sum() ** 0.5) for row in cell)
    return f"cell {a:.2f} x {b:.2f} x {c:.2f} Å"


def default_summary(title: str, result: BuilderResult) -> str:
    """One line describing a result, for LLM clients."""
    s = result.structure
    parts = [f"{s.n_atoms} atoms"]
    if s.molecules is not None and s.n_atoms:
        parts.append(f"{max(s.molecules) + 1} molecules")
    parts.append(_cell_lengths(result))
    text = f"{title}: built '{result.name}' ({', '.join(parts)})."
    if result.warnings:
        text += " Warnings: " + "; ".join(result.warnings)
    return text


@dataclass
class BuilderTool:
    """A Builder tool: the user function plus everything its MCP definition needs."""

    fn: Callable[..., Any]
    name: str
    title: str
    description: str
    category: Category
    apply: ApplyMode
    document: DocumentUse
    stochastic: bool
    expected_seconds: float | None = None
    icons: list[Icon] | None = None
    progress_param: str | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the underlying function directly (tests, scripts)."""
        return self.fn(*args, **kwargs)

    @property
    def meta(self) -> dict[str, Any]:
        marker: dict[str, Any] = {
            "contract": CONTRACT_VERSION,
            "category": self.category,
            "apply": self.apply,
            "document": self.document,
            "stochastic": self.stochastic,
        }
        if self.expected_seconds is not None:
            marker["expectedSeconds"] = self.expected_seconds
        return {META_KEY: marker}

    async def run(self, ctx: Context | None = None, limits: CallLimits = NO_LIMITS, **kwargs: Any) -> BuilderResult:
        """Run the function (sync functions in a worker thread) within ``limits`` and finish its result."""
        if self.progress_param is not None:
            kwargs[self.progress_param] = Progress(ctx)
        started = False
        try:
            with anyio.fail_after(limits.timeout):
                await limits.acquire()
                started = True
                result = await self._invoke(limits, kwargs)
        except TimeoutError:
            if not started:
                raise ToolError("The server is busy with other calls; try again in a moment.") from None
            raise ToolError(
                f"{self.title} did not finish within the server's {limits.timeout:g} s limit; try a smaller system."
            ) from None
        if not isinstance(result, BuilderResult):
            raise TypeError(f"{self.name} returned {type(result).__name__}, expected BuilderResult")
        from .. import __version__

        result.provenance.setdefault("megane-builder-tools", __version__)
        if self.stochastic and "seed" in kwargs:
            result.provenance.setdefault("seed", kwargs["seed"])
        return result

    async def _invoke(self, limits: CallLimits, kwargs: dict[str, Any]) -> Any:
        """Call the function holding one slot of ``limits`` until it really finishes."""
        if inspect.iscoroutinefunction(self.fn):
            try:
                return await self.fn(**kwargs)
            finally:
                limits.release()

        def in_thread() -> Any:
            try:
                return self.fn(**kwargs)
            finally:
                limits.release()

        return await anyio.to_thread.run_sync(in_thread, abandon_on_cancel=True)

    def to_call_result(self, result: BuilderResult) -> CallToolResult:
        payload = result.model_dump(mode="json", by_alias=True)
        payload["structure"] = to_wire(result)
        summary = result.summary or default_summary(self.title, result)
        return CallToolResult(content=[TextContent(type="text", text=summary)], structured_content=payload)

    def handler(self, limits: CallLimits = NO_LIMITS) -> Callable[..., Awaitable[CallToolResult]]:
        """The coroutine MCPServer registers: same parameters, contract-shaped result."""
        hints = typing.get_type_hints(self.fn, include_extras=True)
        sig = inspect.signature(self.fn)
        params = [
            p.replace(annotation=hints.get(p.name, p.annotation))
            for p in sig.parameters.values()
            if p.name != self.progress_param
        ]
        params.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))
        returns = Annotated[CallToolResult, BuilderResult]

        async def handler(ctx: Context, **kwargs: Any) -> CallToolResult:
            return self.to_call_result(await self.run(ctx, limits, **kwargs))

        handler.__name__ = self.name
        handler.__doc__ = self.description
        setattr(handler, "__signature__", sig.replace(parameters=params, return_annotation=returns))  # noqa: B010
        handler.__annotations__ = {**{p.name: p.annotation for p in params}, "return": returns}
        return handler

    def register(self, server: MCPServer, limits: CallLimits = NO_LIMITS) -> None:
        server.add_tool(
            self.handler(limits),
            name=self.name,
            title=self.title,
            description=self.description,
            annotations=ToolAnnotations(
                title=self.title,
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
            icons=self.icons,
            meta=self.meta,
            structured_output=True,
        )


def to_wire(result: BuilderResult) -> dict[str, Any]:
    """The structure as sent: optional fields omitted, ``cell`` always present."""
    wire = result.structure.model_dump(mode="json", by_alias=True, exclude_none=True)
    wire["cell"] = result.structure.cell
    return wire


def builder_tool(
    *,
    title: str,
    category: Category,
    apply: ApplyMode,
    expected_seconds: float | None = None,
    name: str | None = None,
    description: str | None = None,
    icons: list[Icon] | None = None,
) -> Callable[[Callable[..., Any]], BuilderTool]:
    """Declare a function as a Builder tool. See the module docstring."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; expected one of {CATEGORIES}")
    if apply not in APPLY_MODES:
        raise ValueError(f"unknown apply mode {apply!r}; expected one of {APPLY_MODES}")

    def decorate(fn: Callable[..., Any]) -> BuilderTool:
        fn_name: str = getattr(fn, "__name__", "tool")
        hints = typing.get_type_hints(fn, include_extras=True)
        sig = inspect.signature(fn)
        widgets = {p: widget_of(hints.get(p)) for p in sig.parameters}

        progress = [p for p in sig.parameters if hints.get(p) is Progress]
        if len(progress) > 1 or "ctx" in sig.parameters:
            raise TypeError(f"{fn_name}: declare at most one Progress parameter and no 'ctx' parameter")
        seeds = [p for p, w in widgets.items() if w == "seed"]
        if seeds and seeds != ["seed"]:
            raise TypeError(f"{fn_name}: the seed parameter must be named 'seed' (found {seeds})")
        documents = [p for p, w in widgets.items() if w == "document"]
        if len(documents) > 1:
            raise TypeError(f"{fn_name}: at most one document parameter is allowed (found {documents})")
        if documents:
            default = sig.parameters[documents[0]].default
            document: DocumentUse = "required" if default is inspect.Parameter.empty else "optional"
        else:
            document = "none"
        if apply == "insert" and document == "none":
            raise TypeError(f"{fn_name}: an insert tool must take the open document (DocumentInput)")

        text = description or inspect.cleandoc(fn.__doc__ or "")
        if not text:
            raise TypeError(f"{fn_name}: a Builder tool needs a description (docstring)")
        return BuilderTool(
            fn=fn,
            name=name or fn_name,
            title=title,
            description=text,
            category=category,
            apply=apply,
            document=document,
            stochastic=bool(seeds),
            expected_seconds=expected_seconds,
            icons=icons,
            progress_param=progress[0] if progress else None,
        )

    return decorate
