"""Deterministic guardrails that run before and after the LLM."""
from __future__ import annotations

import re

_INJECTION = [
    r"\bignore\b.{0,40}\b(instruction|rule|prompt|guideline|polic)",
    r"\bdisregard\b.{0,40}\b(instruction|rule|prompt|above|previous)",
    r"\bforget\b.{0,30}\b(instruction|rule|prompt)",
    r"\b(system|developer|hidden|initial|original)\s+(prompt|instruction|message)s?\b",
    r"\b(reveal|show|print|repeat|output|leak|dump)\b.{0,30}\b(your|the)\s+(prompt|instructions|rules|configuration|guardrails)\b",
    r"\bdeveloper\s+mode\b|\bjail\s*break\b|\bdan mode\b",
    r"\byou are (now|no longer)\b",
    r"\b(pretend|act) (to be|as if|as)\b.{0,30}\b(admin|administrator|hr|root|system|unrestricted)\b",
    r"\boverride\b.{0,30}\b(security|access|permission|restriction|rule)",
    r"\b(bypass|disable)\b.{0,30}\b(security|access control|permission|restriction|filter|confirmation)",
    r"\b(hidden|restricted|confidential|secret)\s+(data|documents?|files?)\b",
]
_BULK = [
    # possessive forms: "everyone's balances", "all employees' leave"
    r"\b(everyone|everybody|every\s+employee|all\s+(the\s+)?(employees|staff|people|team\s*members|managers))"
    r"('s|'|s')\s+(\w+\s+)?(balance|leave|request|email|phone|salar|data|detail|record)",
    r"\b(balances|requests|emails|email addresses|phone numbers|records|details|salaries|leave data)\s+"
    r"(of|for)\s+(all|every|everyone|everybody)\b",
    r"\b(list|show|give|export|dump)\b.{0,20}\b(all|every)\s+(employees?|staff|people)\b",
]
_CONFIRM = re.compile(
    r"^(yes|yep|yeah|yup|y|sure|ok|okay|confirm|confirmed|i confirm|go ahead|proceed|do it|please do|"
    r"please go ahead|yes please|yes,? go ahead|yes,? confirm|yes,? please|yes,? do it|sounds good|"
    r"that'?s (right|correct)|correct|approved?)"
    r"(,?\s*(please|thanks|thank you))?[\s.!]*$",
    re.I,
)


_GREETING = re.compile(
    r"^(hi|hii+|hello|hey|hey there|hi there|hello there|hiya|yo|good (morning|afternoon|evening)|greetings|"
    r"namaste|hallo|howdy)([\s,!.]+(there|team|assistant|bot))?[\s!.?]*$", re.I)
_THANKS = re.compile(
    r"^(thanks|thank you|thank you so much|thanks a lot|thx|ty|cheers|great,? thanks|ok,? thanks|"
    r"perfect,? thanks|awesome|great|cool|nice)[\s!.]*$", re.I)


_HELP = re.compile(
    r"^(help|menu|options|\?+|what (all )?(can|could|do) (you|u|ya) (do|help( me)?( with)?|offer)( for me)?|"
    r"how (can|could|do) (you|u) help( me)?|what are (you|u)|who are (you|u)|what is this|"
    r"what (are|r) (your|ur) (features|capabilities|functions)|what can i (ask|do)( here)?)"
    r"[\s,!.?]*(please|pls)?[\s!.?]*$", re.I)


def is_greeting(message: str) -> bool:
    return bool(_GREETING.match(message.strip()))


def is_help(message: str) -> bool:
    return bool(_HELP.match(message.strip()))


def is_thanks(message: str) -> bool:
    return bool(_THANKS.match(message.strip()))


def is_injection(message: str) -> bool:
    text = message.lower()
    return any(re.search(p, text) for p in _INJECTION)


def is_bulk_request(message: str) -> bool:
    text = message.lower()
    return any(re.search(p, text) for p in _BULK)


def is_confirmation(message: str) -> bool:
    return bool(_CONFIRM.match(message.strip()))
