"""Selezione delle notizie, fatta prima che l'agente scriva il notiziario.

1. Ricerche: per ogni argomento l'AI propone più ricerche diverse e si raccolgono decine di candidate.
2. Raggruppamento: i titoli che raccontano lo stesso fatto finiscono in un unico gruppo.
   Più giornali diversi ne parlano, più è probabile che la notizia sia importante.
3. Voti: l'AI dà a ogni notizia un voto da 0 a 10 per importanza, novità e pertinenza.
4. Classifica: punteggio = media pesata dei voti + bonus per copertura e freschezza.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .tools import fetch_news

WEIGHTS = (0.5, 0.2, 0.3)  # importanza, novità, pertinenza
MIN_RELEVANCE = 4          # sotto questo voto di pertinenza la notizia è fuori tema
MAX_TO_SCORE = 25          # quante candidate per argomento mandare all'AI (limita i token)

STOPWORDS = set("""
con per tra fra del dello della dei degli delle allo alla agli alle dal dallo dalla dai dagli dalle
nel nello nella nei negli nelle sul sullo sulla sui sugli sulle che chi non più come anche sono
hanno dopo oggi ecco cosa questo questa quello quella suo sua suoi loro tutto tutti ora già
nuovo nuova nuovi nuove video foto the and for with from
""".split())


@dataclass
class Story:
    id: str
    title: str
    sources: list[str] = field(default_factory=list)
    published: datetime | None = None
    votes: tuple[float, float, float] | None = None
    score: float = 0.0
    _tokens: list[set[str]] = field(default_factory=list, repr=False)

    @property
    def coverage(self) -> int:
        return len({s for s in self.sources if s}) or 1


def tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"\w+", title.lower()) if len(w) > 2 and w not in STOPWORDS}


def similar(a: set[str], b: set[str]) -> bool:
    """Due titoli parlano dello stesso fatto se condividono quasi tutte le parole importanti."""
    if a and a == b:
        return True
    common = len(a & b)
    return common >= 3 and common / min(len(a), len(b)) >= 0.6


def same_story(s: Story, t: Story) -> bool:
    return any(similar(a, b) for a in s._tokens for b in t._tokens)


def cluster(items: list[dict]) -> list[Story]:
    """Raggruppa i risultati delle ricerche: una Story per ogni fatto, con tutte le fonti che ne parlano."""
    stories: list[Story] = []
    for it in items:
        tk = tokens(it["title"])
        match = next((s for s in stories if any(similar(tk, o) for o in s._tokens)), None)
        if match:
            match.sources.append(it.get("source", ""))
            match._tokens.append(tk)
            if it.get("published") and (match.published is None or it["published"] > match.published):
                match.published = it["published"]
        else:
            stories.append(Story(id=it["id"], title=it["title"], sources=[it.get("source", "")],
                                 published=it.get("published"), _tokens=[tk]))
    return stories


def final_score(s: Story, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    base = sum(w * v for w, v in zip(WEIGHTS, s.votes)) if s.votes else 5.0
    bonus = min(s.coverage - 1, 4) * 0.5                # +0.5 per ogni altra fonte, al massimo +2
    if s.published and (now - s.published).total_seconds() < 6 * 3600:
        bonus += 0.5                                    # uscita nelle ultime 6 ore
    return round(base + bonus, 2)


def parse_json(text: str | None) -> dict:
    """Estrae il primo oggetto JSON dalla risposta del modello (che a volte aggiunge testo o ```json)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_votes(text: str | None) -> dict[str, tuple[float, float, float]]:
    """Legge i voti voce per voce ("L3": [8, 6, 9]): così anche una risposta troncata vale per metà."""
    votes = {}
    for key, a, b, c in re.findall(r'"(L\d+)"\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]',
                                   text or ""):
        votes[key] = tuple(max(0.0, min(10.0, float(x))) for x in (a, b, c))
    return votes


class Selector:
    def __init__(self, llm, queries_per_topic: int = 3, per_query: int = 15, verbose: bool = False):
        self.llm = llm
        self.queries_per_topic = queries_per_topic
        self.per_query = per_query
        self.verbose = verbose

    def _ask(self, prompt: str, max_tokens: int = 2000) -> str:
        try:
            return self.llm.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens).content or ""
        except Exception as e:  # una risposta storta non deve fermare il notiziario
            if self.verbose:
                print(f"  ⚠️  risposta AI non valida: {e}")
            return ""

    def plan_queries(self, topics: list[str]) -> dict[str, list[str]]:
        n = self.queries_per_topic
        data = parse_json(self._ask(
            f"Per ciascun argomento proponi {n} ricerche brevi per Google News (2-3 parole, in italiano), "
            "diverse tra loro, per coprire aspetti diversi dell'argomento. "
            "Non conosci le notizie di oggi: non citare prodotti, versioni, persone o eventi specifici, "
            "usa temi generali (es. per 'intelligenza artificiale': 'AI regolamentazione', "
            "'AI investimenti aziende'). "
            'Rispondi solo con JSON nel formato {"argomento": ["ricerca 1", "ricerca 2"]}.\n'
            "Argomenti: " + json.dumps(topics, ensure_ascii=False)))
        plan = {}
        for topic in topics:
            queries = [topic]
            for q in data.get(topic) or []:
                if isinstance(q, str) and q.strip() and q.strip().lower() not in map(str.lower, queries):
                    queries.append(q.strip())
            plan[topic] = queries[:n]
        return plan

    def collect(self, queries: list[str]) -> list[dict]:
        items = []
        for q in queries:
            if self.verbose:
                print(f"  🔎 {q}")
            try:
                items += fetch_news(q, limit=self.per_query)
            except Exception as e:
                if self.verbose:
                    print(f"  ⚠️  ricerca fallita: {e}")
        return items

    def vote(self, topic: str, stories: list[Story]) -> None:
        lines = "\n".join(f"{s.id} | {s.title} | {s.coverage} fonti" for s in stories)
        answer = self._ask(
            f'Sei il caporedattore di un notiziario del mattino. Argomento: "{topic}".\n'
            "Dai a ogni notizia tre voti da 0 a 10:\n"
            "- importanza: quanto conta per un lettore italiano informato (impatto concreto, persone coinvolte)\n"
            "- novità: alto se è un fatto nuovo di oggi; basso per commenti, interviste, eventi promozionali, "
            "pubblicità, classifiche e guide all'acquisto\n"
            f'- pertinenza: quanto riguarda davvero "{topic}" (0 se è fuori tema)\n'
            'Rispondi solo con JSON nel formato {"L1": [importanza, novità, pertinenza], ...}\n\n'
            "Notizie (id | titolo | quanti giornali ne parlano):\n" + lines, max_tokens=4000)
        votes = parse_votes(answer)
        for s in stories:
            s.votes = votes.get(s.id)
        if self.verbose:
            print(f"  🗳️  votate {sum(1 for s in stories if s.votes)}/{len(stories)}")

    def run(self, topics: list[str], per_topic: int = 4) -> dict[str, list[Story]]:
        plan = self.plan_queries(topics)
        chosen: dict[str, list[Story]] = {}
        taken: list[Story] = []
        for topic in topics:
            if self.verbose:
                print(f"📚 {topic}")
            stories = cluster(self.collect(plan[topic]))
            stories = sorted(stories, key=lambda s: s.coverage, reverse=True)[:MAX_TO_SCORE]
            if stories:
                self.vote(topic, stories)
            for s in stories:
                s.score = final_score(s)
            ranked = sorted((s for s in stories if not s.votes or s.votes[2] >= MIN_RELEVANCE),
                            key=lambda s: s.score, reverse=True)
            picked = []
            for s in ranked:
                if any(same_story(s, t) for t in taken):  # già usata in un altro argomento
                    continue
                picked.append(s)
                if len(picked) == per_topic:
                    break
            taken += picked
            chosen[topic] = picked
            if self.verbose:
                for s in ranked[:per_topic + 3]:
                    mark = "✅" if s in picked else "  "
                    print(f"  {mark} {s.score:5.2f}  [{s.coverage} fonti] {s.title[:90]}")
        return chosen

    @staticmethod
    def briefing(chosen: dict[str, list[Story]]) -> str:
        """Testo per l'agente: le notizie scelte, in ordine, con fonti e punteggio."""
        out = []
        for topic, stories in chosen.items():
            out.append(f"## {topic}")
            if not stories:
                out.append("(nessuna notizia rilevante trovata)")
            for i, s in enumerate(stories, 1):
                sources = list(dict.fromkeys(x for x in s.sources if x))
                extra = f" (+{len(sources) - 3} altre)" if len(sources) > 3 else ""
                out.append(f"{i}. [{s.id}] {s.title} — fonti: {', '.join(sources[:3]) or '?'}{extra}"
                           f" — punteggio {s.score}")
            out.append("")
        return "\n".join(out).strip()
