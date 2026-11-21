"""Test offline della selezione: ricerche finte e un'AI finta che risponde con JSON."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from agent import selection
from agent.core import Agent
from agent.selection import Selector, Story, cluster, final_score, parse_votes

NEWS = [
    {"id": "L1", "title": "OpenAI presenta il nuovo modello GPT con ragionamento avanzato", "source": "Ansa"},
    {"id": "L2", "title": "Presenta OpenAI modello GPT ragionamento avanzato: cosa cambia", "source": "Repubblica"},
    {"id": "L3", "title": "OpenAI, il modello GPT con ragionamento avanzato è ufficiale", "source": "Wired"},
    {"id": "L4", "title": "Convegno sull'intelligenza artificiale a Padova", "source": "Il Gazzettino"},
    {"id": "L5", "title": "Le migliori offerte del Black Friday sugli smartphone", "source": "Sito Offerte"},
]


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def chat(self, messages, tools=None, max_tokens=2000):
        self.prompts.append(messages[-1]["content"])
        return NS(content=self.replies.pop(0), tool_calls=None)


def test_cluster_groups_same_story_from_different_papers():
    stories = cluster(NEWS)
    assert len(stories) == 3
    gpt = stories[0]
    assert gpt.id == "L1" and gpt.coverage == 3


def test_final_score_rewards_coverage_and_freshness():
    now = datetime(2026, 11, 21, 8, tzinfo=timezone.utc)
    alone = Story("A", "x", ["Ansa"], votes=(8, 8, 8))
    covered = Story("B", "y", ["Ansa", "Sole", "Wired"], published=now - timedelta(hours=1), votes=(8, 8, 8))
    assert final_score(alone, now) == 8.0
    assert final_score(covered, now) == 8.0 + 1.0 + 0.5


def test_selector_ranks_by_votes_and_drops_off_topic(monkeypatch):
    monkeypatch.setattr(selection, "fetch_news", lambda q, limit=15: NEWS)
    llm = FakeLLM([
        json.dumps({"intelligenza artificiale": ["AI modelli", "AI regole"]}),
        '```json\n{"L1": [9, 9, 10], "L4": [3, 4, 8], "L5": [2, 1, 1]}\n```',
    ])
    chosen = Selector(llm).run(["intelligenza artificiale"], per_topic=4)

    titles = [s.title for s in chosen["intelligenza artificiale"]]
    assert titles[0].startswith("OpenAI presenta")
    assert not any("Black Friday" in t for t in titles)  # pertinenza 1: fuori tema
    assert "3 fonti" in llm.prompts[1]


def test_truncated_votes_are_kept():
    truncated = '{\n  "L1": [8, 7, 9],\n  "L2": [4, 5.5, 12],\n  "L19":'
    assert parse_votes(truncated) == {"L1": (8.0, 7.0, 9.0), "L2": (4.0, 5.5, 10.0)}


def test_selector_survives_broken_ai_answers(monkeypatch):
    monkeypatch.setattr(selection, "fetch_news", lambda q, limit=15: NEWS)
    chosen = Selector(FakeLLM(["non è JSON", "nemmeno questo"])).run(["tech"], per_topic=2)
    assert [s.id for s in chosen["tech"]] == ["L1", "L4"]  # ordinate per copertura


def test_same_story_is_not_repeated_across_topics(monkeypatch):
    monkeypatch.setattr(selection, "fetch_news", lambda q, limit=15: NEWS[:3])
    chosen = Selector(FakeLLM(["{}", "{}", "{}"])).run(["AI", "tecnologia"], per_topic=4)
    assert len(chosen["AI"]) == 1 and chosen["tecnologia"] == []


def test_writer_mode_only_reads_articles():
    llm = FakeLLM(["# 🗞️ Notiziario\n- **GPT** [Ansa](L1)"])
    llm.chat = lambda messages, tools=None: (llm.prompts.append({"messages": messages, "tools": tools})
                                            or NS(content="fatto", tool_calls=None))
    briefing = Selector.briefing({"AI": [Story("L1", "OpenAI presenta GPT", ["Ansa", "Wired"], score=9.5)]})
    assert Agent(llm).run(["AI"], briefing=briefing) == "fatto"

    seen = llm.prompts[-1]
    assert [t["function"]["name"] for t in seen["tools"]] == ["read_article"]
    assert "[L1] OpenAI presenta GPT — fonti: Ansa, Wired" in seen["messages"][-1]["content"]
