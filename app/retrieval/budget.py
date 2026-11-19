"""Token counting (tiktoken cl100k_base) and the per-call corpus token budget."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text or ""))


class BudgetExceeded(RuntimeError):
    pass


def pack(items: list, budget: int, text_of=lambda x: x.text) -> tuple[list, int]:
    """Greedy, order-preserving packing of items while the total stays within budget."""
    chosen, used = [], 0
    for it in items:
        n = count_tokens(text_of(it))
        if used + n > budget:
            continue
        chosen.append(it)
        used += n
    return chosen, used
