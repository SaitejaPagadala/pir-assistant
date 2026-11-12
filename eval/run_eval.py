"""Run the golden questions against the live agent (costs a few cents on Haiku).

    python -m eval.run_eval
"""
import os
import sys

import yaml
from dotenv import load_dotenv

load_dotenv()
from rag.agent import answer  # noqa: E402


def main() -> None:
    cases = yaml.safe_load(open("eval/golden.yaml", encoding="utf-8"))
    passed, cost = 0, 0.0
    for c in cases:
        res = answer(c["q"], api_key=os.environ["ANTHROPIC_API_KEY"])
        low = res["text"].lower()
        ok = True
        if c.get("any"):
            ok &= any(s.lower() in low for s in c["any"])
        if c.get("all"):
            ok &= all(s.lower() in low for s in c["all"])
        cited = bool(res["sources"]) or "not in the indexed sources" in low
        passed += ok
        cost += res["est_cost_usd"]
        print(f"{'PASS' if ok else 'FAIL'} | cited={cited} | {c['q']}")
        if not ok:
            print("   ->", res["text"][:300].replace("\n", " "))
    print(f"\n{passed}/{len(cases)} passed, est. cost ${cost:.4f}")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
