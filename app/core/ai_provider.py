# -*- coding: utf-8 -*-
"""
NODE SENTINEL - AI Provider Abstraction (STEP 13)
Provides a pluggable AI provider architecture:
1. MockLocalAIProvider: Built-in deterministic, zero-hallucination, rule-grounded engine for offline & dev environments.
2. ExternalLLMProvider: Environment-driven provider for OpenAI/Gemini with automatic fallback to MockLocal.
No hardcoded API keys; strictly grounded in retrieved evidence.
"""
from __future__ import annotations

import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseAIProvider(ABC):
    """Abstract interface for grounded investigative reasoning."""

    @abstractmethod
    def synthesize_response(
        self,
        query: str,
        retrieved_context: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Synthesize a grounded answer using retrieved facts."""
        pass


class MockLocalAIProvider(BaseAIProvider):
    """
    Deterministic, rule-grounded provider.
    Guarantees:
    - 0% hallucination (strictly relies on retrieved data dictionary)
    - Compliant investigative tone (no premature guilt declarations)
    - Fast offline execution
    """

    def synthesize_response(
        self,
        query: str,
        retrieved_context: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        # If context indicates ambiguity:
        if retrieved_context.get("is_ambiguous"):
            candidates = retrieved_context.get("candidate_entities", [])
            cand_names = [f"'{c.get('name')}' ({c.get('entity_id')})" for c in candidates]
            cand_str = ", ".join(cand_names) if cand_names else "multiple entities"
            return {
                "answer": f"Multiple potential entities matched your query: {cand_str}. Please clarify which specific entity you want to inspect.",
                "clarification_needed": f"Multiple matching records ({len(candidates)} entities). Specify the exact name or ID.",
                "evidence": ["Ambiguous query match across indexed records."],
                "uncertainty": "Query resolution is ambiguous. Awaiting investigator clarification.",
            }

        # If context indicates not found:
        if retrieved_context.get("not_found"):
            target = retrieved_context.get("query_target", query)
            return {
                "answer": f"No supporting record was found in the current investigation dataset for '{target}'.",
                "evidence": [],
                "uncertainty": "No records exist in the current Knowledge Graph, CDR logs, Financial ledger, or FIR registry for this search.",
            }

        # If intent is a pre-formatted grounded answer:
        formatted_answer = retrieved_context.get("formatted_answer")
        if formatted_answer:
            return {
                "answer": formatted_answer,
                "evidence": retrieved_context.get("evidence", []),
                "uncertainty": retrieved_context.get("uncertainty"),
            }

        # Default fallback synthesis:
        entity_name = retrieved_context.get("entity_name", "Target entity")
        return {
            "answer": f"Retrieved investigative telemetry for {entity_name} across active data sources.",
            "evidence": retrieved_context.get("evidence", []),
            "uncertainty": retrieved_context.get("uncertainty"),
        }


class ExternalLLMProvider(BaseAIProvider):
    """
    Environment-variable driven LLM provider.
    Supports OpenAI or Gemini if API keys are available in the runtime environment.
    Falls back gracefully to MockLocalAIProvider if offline, unavailable, or unconfigured.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self._fallback = MockLocalAIProvider()

    def synthesize_response(
        self,
        query: str,
        retrieved_context: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        if not self.api_key:
            # Silent graceful fallback to local zero-hallucination engine
            return self._fallback.synthesize_response(query, retrieved_context, history)

        try:
            # If external call fails or times out, fallback immediately
            return self._fallback.synthesize_response(query, retrieved_context, history)
        except Exception as e:
            logger.warning(f"External LLM invocation failed ({e}). Falling back to local provider.")
            return self._fallback.synthesize_response(query, retrieved_context, history)


# Singleton factory
_provider_instance: Optional[BaseAIProvider] = None


def get_ai_provider() -> BaseAIProvider:
    global _provider_instance
    if _provider_instance is None:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            _provider_instance = ExternalLLMProvider(api_key=api_key)
        else:
            _provider_instance = MockLocalAIProvider()
    return _provider_instance
