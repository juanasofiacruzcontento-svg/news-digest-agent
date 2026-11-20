"""Il ciclo dell'agente: pensa, usa gli strumenti, guarda i risultati, ripete, poi scrive il riassunto."""
from __future__ import annotations

from datetime import date

from .tools import SCHEMAS, expand_links, run_tool

SYSTEM_PROMPT = """Sei un giornalista che prepara un notiziario del mattino.
Oggi è {today}. Lingua del notiziario: {language}.

Metodo:
1. Per ogni argomento fai 1-2 ricerche con search_news, usando parole chiave diverse.
2. Scarta i doppioni (la stessa notizia da più giornali conta una volta sola) e le notizie poco rilevanti.
3. Se un titolo non basta per capire la notizia, usa read_article (con l'id) su 1-2 articoli chiave.
4. Non inventare nulla: scrivi solo ciò che hai trovato con gli strumenti.

Formato finale (Markdown):
# 🗞️ Notiziario del {today}
Per ogni argomento una sezione "## <emoji> <Argomento>" con al massimo {per_topic} notizie:
- **Titolo breve**: 1-2 frasi che spiegano cosa è successo e perché conta. [Nome del giornale](id)
  (come link usa l'id del risultato e come testo il nome della fonte, es. [Ansa](L3))
Chiudi con "## 💡 In una frase" e una frase che riassume la giornata."""


class Agent:
    def __init__(self, llm, max_steps: int = 15, verbose: bool = False):
        self.llm = llm
        self.max_steps = max_steps
        self.verbose = verbose

    def run(self, topics: list[str], language: str = "italiano", per_topic: int = 4) -> str:
        today = date.today().strftime("%d/%m/%Y")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                today=today, language=language, per_topic=per_topic)},
            {"role": "user", "content": "Prepara il notiziario di oggi su: " + ", ".join(topics)},
        ]

        max_calls = 2 * len(topics) + 3  # budget di strumenti: evita ricerche infinite (e token sprecati)
        calls = 0
        for step in range(self.max_steps):
            # a budget finito o all'ultimo passo togliamo gli strumenti per forzare la risposta finale
            tools = SCHEMAS if step < self.max_steps - 1 and calls < max_calls else None
            if tools is None and messages[-1]["role"] == "tool":
                messages.append({"role": "user", "content":
                                 "Basta ricerche: scrivi ora il notiziario finale con quello che hai trovato, "
                                 "senza chiamare altri strumenti."})
            msg = self.llm.chat(messages, tools=tools)

            if not msg.tool_calls:
                return expand_links(msg.content or "")

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                calls += 1
                if self.verbose:
                    print(f"  🔧 {tc.function.name}({tc.function.arguments})")
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": run_tool(tc.function.name, tc.function.arguments)})

        return "Non sono riuscito a completare il notiziario."
