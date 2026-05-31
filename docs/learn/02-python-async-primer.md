# 01 · Python & async primer (for .NET developers)

You don't need to become a Python expert to understand this codebase, but you need the
essentials. This page maps Python concepts to C# ones.

## Running Python

- There's no compile step. `python app.py` runs a file. The web server is started with
  `uvicorn app.main:app` — meaning "import the module `app/main.py`, find the variable `app`,
  and serve it."
- **Modules & packages.** A `.py` file is a *module*. A folder with an `__init__.py` file is a
  *package* (a namespace). `from app.core.config import settings` imports the `settings` object
  from `app/core/config.py`. It's like `using App.Core;` + referencing a static member, except
  imports are **per-file** and explicit.
- **Indentation is syntax.** No braces `{}`. A block is defined by indentation (4 spaces). The
  colon `:` starts a block.

```python
def greet(name: str) -> str:        # method signature with type hints
    if name:                        # ':' opens a block; indentation = the body
        return f"Hello {name}"      # f"..." is string interpolation, like $"..."
    return "Hello stranger"
```

## Dependencies & environments

| Python | .NET |
|--------|------|
| `requirements.txt` (list of packages + versions) | `.csproj` `<PackageReference>` |
| `pip install -r requirements.txt` | `dotnet restore` |
| **virtual environment** (`venv`) — an isolated folder of packages per project | (roughly) a per-project package cache; .NET isolates by default |
| PyPI (pypi.org) | NuGet.org |

In this project you rarely run `pip` directly — Docker does it when the image is built
(`backend/Dockerfile`). The `venv/` folder you might see locally is just an editor convenience.

## Types are optional but used everywhere here

Python is dynamically typed, but supports **type hints** (`name: str`, `-> str`). They're not
enforced at runtime by Python itself, but:
- Editors and tools (and `mypy`) use them for checks, like C# types.
- **Pydantic** and **FastAPI** *do* enforce them at the edges (request parsing) — more on that
  later. This is the key trick that makes a dynamically-typed language feel safe for an API.

```python
from typing import Optional, List       # generics live in `typing`
def f(x: int, y: Optional[str] = None) -> List[int]: ...
#        int    string?   default          List<int>
```

`Optional[str]` is `str | None` — i.e., C#'s `string?`. `List[int]` is `List<int>`.

## Classes, dataclasses, and "DTOs"

```python
from dataclasses import dataclass

@dataclass                  # like a C# record — auto __init__, equality, etc.
class RetrievedChunk:
    chunk_id: str
    content: str
    score: float = 0.0      # default value
```

`@dataclass` is a **decorator** (see below). A `@dataclass` ≈ a C# `record`.

`self` is the explicit `this`. Every instance method takes `self` as the first parameter:

```python
class EmbeddingService:
    def __init__(self, model: str):    # the constructor is always __init__
        self.model = model             # 'self.x = ...' declares+sets a field
    def dim(self) -> int:
        return 1536 if self.model == "..." else 768
```

## Decorators (the `@thing` lines)

A decorator wraps a function/class to add behavior. You'll see these constantly:

```python
@router.post("/login")      # registers this function as a POST handler (like [HttpPost])
async def login(...): ...

@dataclass                  # adds constructor/equality to the class
class Foo: ...

@staticmethod               # a method that doesn't take 'self' (like C# static method)
def helper(): ...
```

Mentally: `@x` above `def f` is roughly C# attributes **that actually run code** — closer to a
middleware/aspect than a pure attribute.

## `async`/`await` — similar to C#, but know the differences

The syntax is familiar:

```python
async def get_user(db, user_id) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
```

- `async def` ≈ `async Task<T>`; `await` is the same idea (yield while waiting on I/O).
- A coroutine (the thing `async def` returns) is like a `Task` — but it **doesn't start until
  awaited** (lazier than a .NET `Task`, which starts immediately).
- **The event loop.** Python runs async code on a single-threaded *event loop* (like Node.js).
  ASP.NET Core uses a thread pool; here, one thread interleaves many awaiting requests. The big
  rule: **never do blocking/CPU-heavy work on the event loop** — it freezes *all* requests. For
  CPU work you offload to a thread (`asyncio.to_thread(...)`, which you'll see used for the
  local re-ranker) or to the Celery worker.
- There's no `ConfigureAwait`, no `SynchronizationContext`. Simpler, but the "don't block the
  loop" rule is stricter.

**Why this matters in this repo:** the API is async end-to-end so one process can handle many
concurrent requests while they wait on the database or the LLM. The Celery worker, by contrast,
runs async code by spinning up an event loop per process (see the `run_async` helper in
`backend/app/services/processing/tasks.py`).

## Context managers (`with` / `async with`)

`with` guarantees cleanup, like C# `using`:

```python
async with AsyncSessionLocal() as session:   # like 'await using var session = ...'
    ...                                       # session is open in here
# session is closed here, even if an exception was thrown
```

You'll see `async with` for database sessions and HTTP clients.

## Other syntax you'll meet

```python
items = [c.content for c in chunks if c.score > 0]   # list comprehension = LINQ Select/Where
names = {u.id: u.name for u in users}                # dict comprehension
first = chunks[0]; last = chunks[-1]                  # negative index = from the end
top = chunks[:5]                                       # slice = chunks.Take(5)
a, b = pair                                            # tuple unpacking (deconstruction)
data.get("key", default)                              # dict TryGetValue with a default
f"{x:.2f}"                                            # format spec, like {x:F2}
```

- **Truthiness:** `if not chunks:` means "if the list is empty/None". Empty collections,
  `0`, `""`, and `None` are all "falsy". (In C# you'd write `if (chunks is null || chunks.Count == 0)`.)
- **`None`** is `null`.
- **Exceptions:** `raise ValueError("bad")` / `try: ... except SomeError as e: ...` —
  same idea as `throw` / `try/catch`.

## You now know enough

You can read ~90% of this codebase with the above. When you hit something unfamiliar, it's
usually a library API (FastAPI, SQLAlchemy), not the language — and those get explained on the
following pages.

---

Prev: [Orientation](01-orientation.md) · Next: [The request lifecycle →](03-request-lifecycle.md)
