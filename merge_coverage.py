import gzip
import json
import sys


def main():
    inputs = [json.load(gzip.open(path, "rt")) for path in sys.argv[1:-1]]
    contexts = sorted(set().union(*(set(x["contexts"]) for x in inputs)))
    lookup = {name: i for i, name in enumerate(contexts)}
    files = {}
    for data in inputs:
        ids = [lookup[x] for x in data["contexts"]]
        for path, lines in data["files"].items():
            dest = files.setdefault(path, {})
            for line, values in lines.items():
                dest.setdefault(line, set()).update(ids[i] for i in values)
    obj = {"contexts": contexts, "files": {p: {n: sorted(v) for n, v in lines.items()} for p, lines in files.items()}}
    with gzip.open(sys.argv[-1], "wt") as f:
        json.dump(obj, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
