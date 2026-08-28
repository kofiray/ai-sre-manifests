#!/usr/bin/env python3
"""Which manifest KEYS a pull request actually changes (SPEC §4, T1 auto-merge).

`path_policy.classify` will only auto-merge a T1 pull request when every changed
key is in `RUNTIME_KEY_ALLOWLIST` — a restart-annotation bump or a bounded
replica nudge. That rule is the entire difference between "the agent may restart
a workload unattended" and "the agent may change anything under runtime/
unattended", so the list of changed keys has to be computed from the diff rather
than asserted by whoever opened the pull request.

Keys are compared as flattened dotted paths across the two YAML documents, keyed
by (kind, namespace, name) so a document that moves within a file is not read as
a wholesale rewrite. Anything the comparison cannot make sense of returns a
sentinel key that is NOT in any allowlist, so an unparseable diff fails closed
rather than silently presenting as "no keys changed" — which would auto-merge.
"""
import json
import sys

try:
    import yaml
except ImportError:                                   # pragma: no cover
    yaml = None

UNKNOWN = "__unparseable__"


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        # Index lists positionally. A reordered list reads as changed, which is
        # the safe direction: it costs a review, it does not grant a merge.
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _docs(text):
    if yaml is None:
        return None
    try:
        docs = [d for d in yaml.safe_load_all(text or "") if isinstance(d, dict)]
    except Exception:
        return None
    keyed = {}
    for d in docs:
        md = d.get("metadata") or {}
        keyed[(d.get("kind"), md.get("namespace"), md.get("name"))] = d
    return keyed


def changed_keys(before_text, after_text):
    """Dotted keys whose value differs between the two YAML texts."""
    before, after = _docs(before_text), _docs(after_text)
    if before is None or after is None:
        return [UNKNOWN]
    keys = set()
    for ident in set(before) | set(after):
        b = _flatten(before.get(ident, {}))
        a = _flatten(after.get(ident, {}))
        for k in set(b) | set(a):
            if b.get(k, _MISSING) != a.get(k, _MISSING):
                keys.add(k)
    return sorted(keys)


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()


def main():
    """stdin: {"files": [{"path":..., "before":..., "after":...}]}  ->  keys"""
    payload = json.load(sys.stdin)
    keys = set()
    for f in payload.get("files", []):
        keys.update(changed_keys(f.get("before", ""), f.get("after", "")))
    print(json.dumps({"changed_keys": sorted(keys)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
