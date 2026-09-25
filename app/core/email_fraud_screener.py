# -*- coding: utf-8 -*-
"""
Email Fraud Screener for NODE SENTINEL.

Deterministic, explainable heuristic scorer for fraud/scam email text
(e.g. fraud_email_.csv: Text, Class). Consistent with the engine's
explainable-intelligence style: every point traces to a matched indicator.

Outputs score 0-100 + level (LOW/ELEVATED/HIGH/CRITICAL) + indicator list.
Decision-support only — not proof of fraud.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# (pattern, points, label) — weights sum well above 100; score is capped.
FRAUD_PATTERNS: List[Tuple[str, int, str]] = [
    (r"dear friend|greetings to you|wish to accost you", 25, "Advance-fee solicitation opener"),
    (r"inheritance|next of kin|deceased (customer|client)|unclaimed (fund|money|deposit)", 25, "Inheritance / unclaimed-funds lure"),
    (r"lottery|won (the |a )?(prize|award)|lucky winner|claim your (prize|winnings|reward)", 25, "Lottery / prize lure"),
    (r"bank (draft|transfer)|wire (transfer|funds)|western union|money ?gram", 12, "Wire-transfer instruction"),
    (r"otp|one[- ]time password|verify (your|the) (account|identity)|kyc (update|suspend|verif)|account (suspend|block|deactivat)", 20, "Credential/OTP phishing (KYC/lockout pretext)"),
    (r"urgent|immediate(ly)?|act (now|fast)|within 24 hours|last (warning|chance|notice)", 10, "Artificial urgency / pressure"),
    (r"confidential|keep (this |it )?secret|do not (disclose|reveal|tell)", 10, "Secrecy request"),
    (r"processing fee|advance fee|release fee|legal fee|pay .* to (release|claim|unlock)", 20, "Upfront-fee demand"),
    (r"click (here|the link|below)|verify (here|below)|http[s]?://|www\.", 8, "Suspicious link / call-to-action"),
    (r"congratulations|selected as|you have been (chosen|selected)", 10, "Unsolicited selection claim"),
    (r"foreign (account|bank|partner)|offshore|diplomat|consignment (box|of)|trunk box", 12, "Cross-border consignment story"),
    (r"pastor|reverend|barrister|prince|princess|general\b|ambassador", 6, "Authority-figure impersonation"),
    (r"million (dollars|usd|pounds)|usd\s?[\d,]+|\$[\d,]+", 8, "Large money bait figure"),
    (r"reply (to|with)|send .* (passport|id|details|account number)|full (name|address|contact)", 8, "PII harvesting request"),
    (r"risk[- ]free|guaranteed|100% (safe|legal|genuine)|no risk", 8, "Too-good-to-be-true guarantee"),
    (r"beneficiar|funds? (transfer|release)|central bank|apex bank|clearing house", 8, "Banking-authority name-drop"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), pts, label) for p, pts, label in FRAUD_PATTERNS]

PHONE_RE = re.compile(r"(?:\+91[\-\s]?)?[6-9]\d{9}\b")
URL_RE = re.compile(r"https?://|www\.\S+|bit\.ly/\S+")
MONEY_RE = re.compile(r"(?:₹|INR|USD|\$|Rs\.?)\s?[\d,]+")


def score_email(text: Any) -> Dict[str, Any]:
    """Score a single email body. Returns score/level/indicators dict."""
    body = str(text or "")
    indicators: List[Dict[str, Any]] = []
    score = 0
    for rx, pts, label in _COMPILED:
        m = rx.search(body)
        if m:
            excerpt = m.group(0)[:60]
            indicators.append({"indicator": label, "points": pts, "match": excerpt})
            score += pts
    # Structural bonuses (capped contribution)
    if URL_RE.search(body):
        indicators.append({"indicator": "Embedded URL present", "points": 5, "match": "url"})
        score += 5
    if PHONE_RE.search(body):
        indicators.append({"indicator": "Contact phone number present", "points": 5, "match": "phone"})
        score += 5
    if MONEY_RE.search(body):
        indicators.append({"indicator": "Money figure quoted", "points": 5, "match": "amount"})
        score += 5
    if len(body) < 60 and score > 0:
        indicators.append({"indicator": "Very short lure message", "points": 5, "match": f"len={len(body)}"})
        score += 5

    score = max(0, min(100, score))
    if score >= 60:
        level = "CRITICAL"
    elif score >= 35:
        level = "HIGH"
    elif score >= 15:
        level = "ELEVATED"
    else:
        level = "LOW"
    return {
        "score": score,
        "level": level,
        "indicator_count": len(indicators),
        "indicators": sorted(indicators, key=lambda d: d["points"], reverse=True)[:8],
    }
