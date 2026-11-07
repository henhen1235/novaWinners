"""python -m flyagent table   (rebuild flyagent/response_table.json; needs numba, see table.py)"""

import sys

if __name__ == "__main__":
    if sys.argv[1:] != ["table"]:
        sys.exit("usage: python -m flyagent table")
    from flyagent.table import build

    body = build()
    print(f"wrote {len(body['entries'])} entries; provenance: {body['provenance']}")
