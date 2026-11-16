"""The simulated user who answers the system's clarifying question in the
clarification evaluation (eval/run_vague.py).
"""
import re

import config

NULL_REPLY = config.NULL_REPLY
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# The last choice-mode reply: {"options", "matched_option", "chosen_value",
# "chosen_rank"}. Written only under USER_SIM=choice.
CHOICE_TRACE = {}


def _words(text):
    return set(_WORD_RE.findall((text or "").lower()))


def choose(withheld, options):
    """(value, matched, rank): the first option whose value's words contain,
    or are contained in, the withheld detail's words; else the detail."""
    want = _words(withheld)
    if want:
        for rank, option in enumerate(options or [], 1):
            have = _words(option.get("value"))
            if have and (have <= want or want <= have):
                return option["value"], True, rank
    return withheld, False, None


def reply(vague_question, withheld, agent_question):
    if config.USER_SIM not in ("template", "choice"):
        raise ValueError("USER_SIM=%r is not implemented" % config.USER_SIM)
    if config.USER_SIM == "template":
        if withheld and withheld.strip():
            return withheld
        return NULL_REPLY
    options = [dict(o) for o in (getattr(agent_question, "options", None) or [])]
    if withheld and withheld.strip():
        value, matched, rank = choose(withheld, options)
    else:
        value, matched, rank = NULL_REPLY, False, None
    CHOICE_TRACE.clear()
    CHOICE_TRACE.update({"options": options, "matched_option": matched,
                         "chosen_value": value, "chosen_rank": rank})
    return value


def take_choice_trace():
    out = dict(CHOICE_TRACE)
    CHOICE_TRACE.clear()
    return out
