"""Auto model router.

Two strategies, used in order:

1. **Heuristic** – fast keyword / pattern matching on the user prompt.
   Catches the obvious cases (code-heavy task → ``codestral``,
   image/screenshot analysis → ``pixtral``, very short request →
   ``mistral-small``) without any API call.

2. **LLM fallback** – when heuristics don't yield a confident choice we
   ask a tiny model (``ministral-3b-latest``) to classify the task.

The router never raises – on failure it returns
``mistral-medium-latest`` as a safe default.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import settings
from .mistral_client import get_client

log = logging.getLogger(__name__)


CODE_HINTS = re.compile(
    r"\b(code|function|class|refactor|debug|stack trace|compile|"
    r"python|javascript|typescript|rust|golang|java|c\+\+|"
    r"npm|pip|cargo|gradle|maven|"
    r"unit test|pytest|jest|"
    r"regex|sql|api endpoint|"
    r"shell|bash|terminal command|kubectl|docker)\b",
    re.IGNORECASE,
)

VISION_HINTS = re.compile(
    r"\b(screenshot|image|picture|photo|see (my|the) screen|look at|"
    r"what.*on (my|the) screen|visual)\b",
    re.IGNORECASE,
)

HEAVY_HINTS = re.compile(
    r"\b(plan|design|architect|analy[sz]e|deep|comprehensive|"
    r"compare.*options|trade-?offs?|investigate|research|"
    r"why does|how would you|long answer)\b",
    re.IGNORECASE,
)

REASONING_HINTS = re.compile(
    r"\b(step by step|reason through|prove|derive|"
    r"calcul|math|logic puzzle|chain of thought)\b",
    re.IGNORECASE,
)

QUICK_HINTS = re.compile(
    r"\b(what time|quick|short|one ?line|tldr|summari[sz]e in)\b",
    re.IGNORECASE,
)


@dataclass
class RouteResult:
    model: str
    reason: str
    via: str  # "heuristic" | "llm" | "default"


def heuristic_route(prompt: str) -> RouteResult | None:
    """Match obvious task types without an API call."""
    word_count = len(prompt.split())

    if VISION_HINTS.search(prompt):
        return RouteResult("pixtral-large-latest", "vision request", "heuristic")

    if CODE_HINTS.search(prompt):
        return RouteResult("codestral-latest", "code/shell request", "heuristic")

    if REASONING_HINTS.search(prompt):
        return RouteResult(
            "magistral-medium-latest", "explicit reasoning request", "heuristic"
        )

    if QUICK_HINTS.search(prompt) or word_count <= 6:
        return RouteResult(
            "mistral-small-latest", "short/simple request", "heuristic"
        )

    if HEAVY_HINTS.search(prompt) or word_count > 80:
        return RouteResult(
            "mistral-large-latest", "complex / multi-step request", "heuristic"
        )

    return None


_CLASSIFIER_PROMPT = """You are a model router for a personal assistant.
Read the user request and reply with exactly ONE of these labels:

  CODE       - programming, shell, devops, debugging
  REASONING  - hard logic, math, multi-step planning
  VISION     - involves looking at screen / images
  HEAVY      - long answer, deep research, design
  QUICK      - trivial chat, one-liner, simple lookup
  GENERAL    - everything else

Only output the single label, nothing else."""


_LABEL_TO_MODEL = {
    "CODE": "codestral-latest",
    "REASONING": "magistral-medium-latest",
    "VISION": "pixtral-large-latest",
    "HEAVY": "mistral-large-latest",
    "QUICK": "mistral-small-latest",
    "GENERAL": "mistral-medium-latest",
}


def llm_route(prompt: str) -> RouteResult:
    """Ask a tiny model to classify the request."""
    try:
        client = get_client()
        resp = client.chat.complete(
            model=settings.router_model,
            messages=[
                {"role": "system", "content": _CLASSIFIER_PROMPT},
                {"role": "user", "content": prompt[:2000]},
            ],
            temperature=0.0,
            max_tokens=4,
        )
        label = (resp.choices[0].message.content or "").strip().upper().split()[0]
        model = _LABEL_TO_MODEL.get(label, "mistral-medium-latest")
        return RouteResult(model, f"classifier said {label}", "llm")
    except Exception as exc:  # network / auth / quota
        log.warning("LLM router failed: %s", exc)
        return RouteResult("mistral-medium-latest", f"router failed: {exc}", "default")


def route(prompt: str) -> RouteResult:
    """Pick the best model id for a prompt."""
    return heuristic_route(prompt) or llm_route(prompt)
