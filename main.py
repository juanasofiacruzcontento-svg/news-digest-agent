"""Avvio: python main.py  (usa config.toml)  oppure  python main.py --topics "calcio,meteo" --dry-run"""
import argparse
import tomllib

from dotenv import load_dotenv

load_dotenv()

from agent import Agent, LLM  # noqa: E402
from agent.delivery import send_email, send_telegram  # noqa: E402
from agent.selection import Selector  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Agente AI che prepara un notiziario quotidiano")
    p.add_argument("--topics", help="Argomenti separati da virgola (sovrascrive config.toml)")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--dry-run", action="store_true", help="Stampa il notiziario senza inviarlo")
    p.add_argument("-v", "--verbose", action="store_true", help="Mostra cosa fa l'agente passo per passo")
    args = p.parse_args()

    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    topics = [t.strip() for t in args.topics.split(",")] if args.topics else cfg["topics"]

    per_topic = cfg.get("news_per_topic", 4)
    print(f"🗞️  Preparo il notiziario su: {', '.join(topics)}")
    llm = LLM(verbose=args.verbose)

    briefing = None
    sel = cfg.get("selezione", {})
    if sel.get("attiva", True):
        try:
            chosen = Selector(llm, queries_per_topic=sel.get("ricerche_per_argomento", 3),
                              per_query=sel.get("notizie_per_ricerca", 15),
                              verbose=args.verbose).run(topics, per_topic)
            if any(chosen.values()):
                briefing = Selector.briefing(chosen)
        except Exception as e:  # se la selezione fallisce l'agente cerca da solo, come prima
            print(f"⚠️  Selezione non riuscita ({e}): l'agente cerca da solo")

    agent = Agent(llm, verbose=args.verbose)
    digest = agent.run(topics, language=cfg.get("language", "italiano"), per_topic=per_topic,
                       briefing=briefing)

    print()
    print(digest)

    if args.dry_run:
        return
    sent = [name for name, fn in (("Telegram", send_telegram), ("email", send_email)) if fn(digest)]
    print("\n📬 Inviato via " + " e ".join(sent) if sent else "\n⚠️  Nessun canale di invio configurato")


if __name__ == "__main__":
    main()