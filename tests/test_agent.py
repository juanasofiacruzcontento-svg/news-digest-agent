"""Test offline: un'AI finta simula le risposte, nessuna chiave o connessione necessaria."""
import json
from types import SimpleNamespace as NS

from agent import tools
from agent.core import Agent
from agent.delivery import markdown_to_telegram, split_message


def call(name, args, id_="c1"):
    return NS(id=id_, function=NS(name=name, arguments=json.dumps(args)))


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools=None):
        self.seen.append({"messages": list(messages), "tools": tools})
        return self.replies.pop(0)


def test_agent_searches_then_writes_digest(monkeypatch):
    monkeypatch.setitem(tools.REGISTRY, "search_news", lambda query, limit=10: [
        {"title": "Nuovo modello AI", "source": "Ansa", "link": "https://x.it/1"}])
    llm = FakeLLM([
        NS(content=None, tool_calls=[call("search_news", {"query": "AI"})]),
        NS(content="# 🗞️ Notiziario\n- **Nuovo modello AI** [Ansa](https://x.it/1)", tool_calls=None),
    ])
    digest = Agent(llm).run(["intelligenza artificiale"])

    assert "Nuovo modello AI" in digest
    tool_result = llm.seen[1]["messages"][-1]
    assert tool_result["role"] == "tool" and "Ansa" in tool_result["content"]


def test_last_step_forces_final_answer():
    looping = NS(content=None, tool_calls=[call("search_news", {"query": "x"})])
    final = NS(content="fine", tool_calls=None)
    llm = FakeLLM([looping, looping, final])
    assert Agent(llm, max_steps=3).run(["x"]) == "fine"
    assert llm.seen[-1]["tools"] is None  # all'ultimo passo niente strumenti


def test_tool_errors_go_back_to_model():
    assert "error" in json.loads(tools.run_tool("non_esiste", "{}"))
    assert "TypeError" in json.loads(tools.run_tool("search_news", '{"sbagliato": 1}'))["error"]


def test_split_message_respects_telegram_limit():
    text = ("riga di prova\n" * 1000) + "x" * 9000
    chunks = split_message(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == text


def test_markdown_to_telegram_html():
    md = "# 🗞️ Notiziario\n## 🤖 AI\n- **Titolo & co**: testo <x>. [Ansa](https://x.it/a?b=1&c=2)"
    out = markdown_to_telegram(md)
    assert out.startswith("<b>🗞️ Notiziario</b>")
    assert "• <b>Titolo &amp; co</b>: testo &lt;x&gt;." in out
    assert '<a href="https://x.it/a?b=1&amp;c=2">Ansa</a>' in out
