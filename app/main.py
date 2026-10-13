import os

from dotenv import load_dotenv

load_dotenv()

from app.agent.graph import build_graph
from app.logging_config import configure_logging, new_correlation_id

configure_logging()


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in .env before running ASTRA.")
    new_correlation_id()
    graph = build_graph()
    graph.invoke({"max_retries": 3})


if __name__ == "__main__":
    main()
