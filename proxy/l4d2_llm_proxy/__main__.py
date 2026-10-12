import argparse
import logging
import os

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="L4D2 LLM bot proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--backend", default=os.environ.get("L4D2_LLM_BACKEND", "mock"))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ["L4D2_LLM_BACKEND"] = args.backend
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
