"""`revisionist web serve --offline-prose` with canned model replies, for browser smoke runs.

No model is called. Pages are written from templates; the aide brief uses the template
aide; a directive is read as the first instrument whose fields can be filled from its
own limits and choices; counsel answers with a fixed line. The cost on each reply is zero.

    REVISIONIST_DB=postgresql://... python web/test/e2e/serve_canned.py [PORT]
"""

import json
import re
import sys
from typing import Any

from revisionist import cli
from revisionist.engine.llm import Reply, Request


def _directive(prompt: str) -> dict[str, Any]:
    found = re.search(r"## Their instruments\n(.*)\n\n## Today", prompt, re.S)
    for lever in json.loads(found.group(1)) if found else []:
        params: dict[str, Any] = {}
        for p in lever["params"]:
            if p["kind"] == "int" and p.get("lo") is not None:
                params[p["name"]] = p["lo"]
            elif p.get("choices"):
                params[p["name"]] = p["choices"][0]["value"]
            elif p["kind"] == "text":
                params[p["name"]] = "As instructed."
            else:
                break
        else:
            return {"lever": lever["lever"], "params": params, "reason": ""}
    return {"lever": "", "params": {}, "reason": "None of your instruments does this."}


class Canned:
    def call(self, request: Request) -> Reply:
        props = set(request.schema.get("properties", {}))
        if request.call_class == "counsel":
            out: Any = {"text": "I would wait a week before deciding; the papers are not all in."}
        elif "lever" in props:
            out = _directive(request.prompt)
        elif "summary" in props:
            out = {"summary": "", "drafts": []}
        else:
            raise RuntimeError(f"no canned reply for {request.call_class}")
        return Reply(out, request.model, 0, 0, 0, 0.0)


if __name__ == "__main__":
    cli.transport_factory = Canned
    sys.argv = ["revisionist", "web", "serve", "--offline-prose", "--port", *sys.argv[1:2]]
    cli.app()
