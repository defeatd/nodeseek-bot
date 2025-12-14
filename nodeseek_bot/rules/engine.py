from __future__ import annotations

import re
from dataclasses import dataclass

from nodeseek_bot.storage.types import ScoreResult


@dataclass
class CompiledRules:
    raw: dict
    score_threshold: float
    explain_top_n: int
    source_confidence: dict
    weights: dict
    keywords: dict
    length_rules: dict
    signals: dict
    block_title_regex: list[re.Pattern]


_DEFAULT_EMPTY = {
    "whitelist": [],
    "blacklist": [],
    "topics": {},
    "trash": [],
}


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    out: list[re.Pattern] = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return out


class RuleEngine:
    def __init__(self, rules: dict):
        self._compiled = self._compile(rules)

    @property
    def rules(self) -> dict:
        return self._compiled.raw

    def _compile(self, rules: dict) -> CompiledRules:
        score_threshold = float(rules.get("score_threshold", 18))
        explain_top_n = int(rules.get("explain_top_n", rules.get("explain_top_n", 6)))

        source_confidence = rules.get("source_confidence", {})
        weights = rules.get("weights", {})
        keywords = rules.get("keywords", _DEFAULT_EMPTY) or _DEFAULT_EMPTY
        length_rules = rules.get("length_rules", {})
        signals = rules.get("signals", {})
        block_title_regex = _compile_patterns((rules.get("block_title_regex") or []))

        return CompiledRules(
            raw=rules,
            score_threshold=score_threshold,
            explain_top_n=explain_top_n,
            source_confidence=source_confidence,
            weights=weights,
            keywords=keywords,
            length_rules=length_rules,
            signals=signals,
            block_title_regex=block_title_regex,
        )

    def score(self, title: str, text: str, source_confidence: str) -> ScoreResult:
        c = self._compiled
        title = title or ""
        text = text or ""
        hay = (title + "\n" + text).casefold()

        contributions: list[dict] = []

        whitelist = [str(x) for x in c.keywords.get("whitelist", [])]
        blacklist = [str(x) for x in c.keywords.get("blacklist", [])]

        for kw in blacklist:
            if kw and kw.casefold() in hay:
                explain = {
                    "decision": "BLACKLIST",
                    "reason": f"blacklist keyword: {kw}",
                    "matched": [kw],
                    "threshold": c.score_threshold,
                }
                return ScoreResult(score_total=-999, decision="BLACKLIST", explain=explain)

        for pat in c.block_title_regex:
            if pat.search(title or ""):
                explain = {
                    "decision": "BLACKLIST",
                    "reason": f"blocked by title regex: {pat.pattern}",
                    "threshold": c.score_threshold,
                }
                return ScoreResult(score_total=-999, decision="BLACKLIST", explain=explain)

        for kw in whitelist:
            if kw and kw.casefold() in hay:
                contributions.append({"name": "whitelist", "score": 999, "reason": f"{kw}"})
                explain = {
                    "decision": "WHITELIST",
                    "threshold": c.score_threshold,
                    "contributions": contributions,
                }
                return ScoreResult(score_total=999, decision="WHITELIST", explain=explain)

        raw_score = 0.0

        # Category keywords
        cat_weights = (c.weights.get("category") or {}).copy()
        topics = c.keywords.get("topics", {}) or {}
        for cat, weight in cat_weights.items():
            kw_list = topics.get(cat) or []
            hit = None
            for kw in kw_list:
                if str(kw).casefold() in hay:
                    hit = str(kw)
                    break
            if hit:
                s = float(weight)
                raw_score += s
                contributions.append({"name": f"category.{cat}", "score": s, "reason": hit})

        # Signals by regex
        sig_weights = (c.weights.get("signals") or {}).copy()
        for sig, conf in (c.signals or {}).items():
            patterns = conf.get("any_regex") if isinstance(conf, dict) else None
            if not patterns:
                continue
            compiled = _compile_patterns([str(p) for p in patterns])
            if any(p.search(title) or p.search(text) for p in compiled):
                s = float(sig_weights.get(sig, 0))
                if s:
                    raw_score += s
                    contributions.append({"name": f"signal.{sig}", "score": s, "reason": "matched"})

        # Length rules
        min_effective = int(c.length_rules.get("min_effective_chars", 180))
        very_short = int(c.length_rules.get("very_short_chars", 80))
        long_threshold = int(c.length_rules.get("long_chars_bonus_threshold", 1200))

        eff_len = len(text.strip())
        if eff_len < very_short:
            penalty = float((c.weights.get("penalties") or {}).get("too_short", -8))
            raw_score += penalty
            contributions.append({"name": "penalty.too_short", "score": penalty, "reason": f"len<{very_short}"})

        if eff_len >= long_threshold:
            bonus = float((c.weights.get("bonuses") or {}).get("long_content", 0))
            if bonus:
                raw_score += bonus
                contributions.append({"name": "bonus.long_content", "score": bonus, "reason": f"len>={long_threshold}"})

        # Low-value patterns
        penalties = c.weights.get("penalties") or {}
        help_words = (c.keywords.get("trash") or [])
        if any(str(w).casefold() in hay for w in help_words) and eff_len < min_effective:
            p = float(penalties.get("pure_help_no_context", -7))
            raw_score += p
            contributions.append({"name": "penalty.pure_help_no_context", "score": p, "reason": "help/trash keywords"})

        if re.search(r"(震惊|必看|不看后悔|速看|重磅)", title):
            p = float(penalties.get("clickbait", -8))
            raw_score += p
            contributions.append({"name": "penalty.clickbait", "score": p, "reason": "title pattern"})

        if re.search(r"(对线|别杠|喷|垃圾|傻|滚|引战)", hay):
            p = float(penalties.get("emotional_or_quarrel", -10))
            raw_score += p
            contributions.append({"name": "penalty.emotional_or_quarrel", "score": p, "reason": "emotional words"})

        if re.search(r"(转载|搬运|转发)", hay):
            p = float(penalties.get("repeated_or_repost_hint", -6))
            raw_score += p
            contributions.append({"name": "penalty.repost", "score": p, "reason": "repost hint"})

        # Apply confidence and RSS-only penalty (after multiplier)
        confidence_factor = float(c.source_confidence.get(source_confidence, 1.0))
        score_total = raw_score * confidence_factor

        rss_only_penalty = 0.0
        if source_confidence == "RSS_ONLY":
            rss_only_penalty = float(penalties.get("rss_only_penalty", -4))
            score_total += rss_only_penalty
            contributions.append({"name": "penalty.rss_only", "score": rss_only_penalty, "reason": "RSS_ONLY"})

        # Decide
        decision = "PUSH" if score_total >= c.score_threshold else "IGNORE"

        # Build explain
        contributions_sorted = sorted(contributions, key=lambda x: abs(float(x.get("score", 0))), reverse=True)
        explain = {
            "threshold": c.score_threshold,
            "raw_score": raw_score,
            "confidence": source_confidence,
            "confidence_factor": confidence_factor,
            "rss_only_penalty": rss_only_penalty,
            "score_total": score_total,
            "decision": decision,
            "contributions": contributions_sorted[: c.explain_top_n],
        }
        return ScoreResult(score_total=score_total, decision=decision, explain=explain)
