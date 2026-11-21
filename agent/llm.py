"""Client LLM multi-provider con failover automatico.

Tutti i provider usati espongono un endpoint compatibile con OpenAI
Chat Completions, quindi basta un solo SDK (`openai`) cambiando base_url.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from openai import OpenAI, APIError, RateLimitError, APIConnectionError


@dataclass
class Provider:
    name: str
    base_url: str
    env_key: str
    model: str


# Modelli di default: cambiali da .env se un modello non è più gratuito.
PROVIDERS = [
    Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
             os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")),
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
             "GEMINI_API_KEY", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
    Provider("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
             os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")),
]


class LLM:
    """Prova i provider configurati in ordine; se uno fallisce passa al successivo."""

    def __init__(self, providers: list[Provider] | None = None, verbose: bool = False):
        self.verbose = verbose
        self.clients = []
        for p in providers or PROVIDERS:
            key = os.getenv(p.env_key)
            if key:
                self.clients.append((p, OpenAI(api_key=key, base_url=p.base_url)))
        if not self.clients:
            raise RuntimeError(
                "Nessuna API key trovata. Imposta almeno una tra: "
                + ", ".join(p.env_key for p in PROVIDERS)
            )

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 2000):
        last_error = None
        for provider, client in self.clients:
            for attempt in range(4):
                try:
                    resp = client.chat.completions.create(
                        model=provider.model,
                        messages=messages,
                        tools=tools or None,
                        temperature=0.2,
                        max_tokens=max_tokens,
                    )
                    if self.verbose:
                        print(f"  [llm] risposta da {provider.name}")
                    return resp.choices[0].message
                except RateLimitError as e:
                    # limite "token al minuto" dei piani gratuiti: aspetta quanto dice il provider
                    last_error = e
                    try:
                        wait = float(e.response.headers.get("retry-after", 0))
                    except (TypeError, ValueError):
                        wait = 0
                    wait = min(max(wait, 5 * (attempt + 1)), 60)
                    if self.verbose:
                        print(f"  [llm] limite di {provider.name} raggiunto, attendo {wait:.0f}s")
                    time.sleep(wait)
                except (APIError, APIConnectionError) as e:
                    last_error = e
                    break  # passa al prossimo provider
            if self.verbose:
                print(f"  [llm] {provider.name} non disponibile, provo il prossimo")
        raise RuntimeError(f"Tutti i provider hanno fallito: {last_error}")
