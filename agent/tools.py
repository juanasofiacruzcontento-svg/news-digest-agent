"""Strumenti dell'agente: cercare notizie (Google News RSS, gratis e senza chiave) e leggere articoli."""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (news-digest-agent)"}
MAX_CHARS = 2000

# I link di Google News sono lunghissimi: al modello diamo un id breve (L1, L2...)
# e alla fine expand_links() rimette gli URL veri nel notiziario. Così si risparmiano token.
LINKS: dict[str, str] = {}


def _short(url: str) -> str:
    for k, v in LINKS.items():
        if v == url:
            return k
    key = f"L{len(LINKS) + 1}"
    LINKS[key] = url
    return key


def expand_links(text: str) -> str:
    """Sostituisce gli id brevi (es. "](L3)") con i link reali."""
    return re.sub(r"\]\((L\d+)\)", lambda m: "](" + LINKS.get(m.group(1), m.group(1)) + ")", text or "")


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text()).strip()


def search_news(query: str, lang: str = "it", country: str = "IT", limit: int = 6) -> list[dict]:
    """Cerca notizie recenti (ultime 24 ore) su Google News."""
    limit = max(1, min(int(limit), 8))
    url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}"
           f"&hl={lang}&gl={country}&ceid={country}:{lang}")
    feed = feedparser.parse(url, request_headers=HEADERS)
    results = []
    for e in feed.entries[:limit]:
        source = e.get("source", {}).get("title", "")
        title = e.get("title", "")
        if source and title.endswith(" - " + source):  # Google aggiunge la fonte al titolo
            title = title[: -len(source) - 3]
        results.append({"id": _short(e.get("link", "")), "title": title[:160], "source": source})
    return results


def read_feed(url: str, limit: int = 6) -> list[dict]:
    """Legge un feed RSS qualsiasi (es. di un giornale)."""
    feed = feedparser.parse(url, request_headers=HEADERS)
    return [{
        "id": _short(e.get("link", "")),
        "title": e.get("title", "")[:160],
        "snippet": _clean(e.get("summary", ""))[:200],
    } for e in feed.entries[:max(1, min(int(limit), 8))]]


def read_article(id: str = "", url: str = "") -> dict:
    """Scarica una pagina e ne estrae il testo principale. Accetta un id (es. "L3") o un URL."""
    url = LINKS.get(id or url, id or url)
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = " ".join(p for p in paragraphs if len(p) > 40)
    return {"title": soup.title.string.strip() if soup.title and soup.title.string else "",
            "text": text[:MAX_CHARS]}


REGISTRY = {"search_news": search_news, "read_feed": read_feed, "read_article": read_article}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_news",
        "description": "Cerca notizie delle ultime 24 ore su Google News. Restituisce id, titolo e fonte.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Parole chiave, es. 'intelligenza artificiale'"},
            "limit": {"type": "integer", "description": "Numero massimo di risultati (default 6, max 8)"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_feed",
        "description": "Legge gli ultimi articoli da un feed RSS specifico.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "read_article",
        "description": "Legge il testo di un articolo, per avere più dettagli.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string", "description": "id del risultato (es. L3) oppure un URL"}}, "required": ["id"]}}},
]


def run_tool(name: str, arguments: str) -> str:
    fn = REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"strumento sconosciuto: {name}"})
    try:
        return json.dumps(fn(**json.loads(arguments or "{}")), ensure_ascii=False)
    except Exception as e:  # l'errore torna al modello, che può riprovare in un altro modo
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
