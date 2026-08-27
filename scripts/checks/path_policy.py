#!/usr/bin/env python3
"""Path-policy status check (SPEC §4, failure-library "autonomy = repo path policy").

The bespoke, self-built status check that classifies a PR into an autonomy tier
and decides merge eligibility. It is the platform's enforcement of autonomy —
never the prompt. Pure function + CLI so its logic is unit-tested locally with no
GitHub API (the P0 gate); the GitHub Actions workflow feeds it real PR context.

Tiers:
  apps/*/runtime/  T1  auto-merge on green (schema-valid, path-scoped, diff-shape allowlisted)
  apps/*/config/   T2  human review required (CODEOWNERS)
  platform/        T3  agent PRs must be drafts (non-draft agent PR -> FAIL)
Fail-closed: any path outside the three -> treat as review-required, no auto-merge.
"""
import json
import re
import sys

AGENT = "sre-agent[bot]"
# T1 runtime diff-shape allowlist: the only key paths an auto-merge PR may change.
RUNTIME_KEY_ALLOWLIST = {
    "spec.template.metadata.annotations.sre-agent/restartedAt",  # declarative rollout restart
    "spec.replicas",                                             # bounded replica nudge
}
_RUNTIME = re.compile(r"^apps/[^/]+/runtime/")
_CONFIG = re.compile(r"^apps/[^/]+/config/")
_PLATFORM = re.compile(r"^platform/")


def _app_of(path: str) -> str | None:
    m = re.match(r"^apps/([^/]+)/", path)
    return m.group(1) if m else None


def classify(author: str, paths: list[str], is_draft: bool,
             changed_keys: list[str] | None = None,
             schema_valid: bool = True) -> dict:
    tiers = set()
    for p in paths:
        if _RUNTIME.match(p):
            tiers.add("T1")
        elif _CONFIG.match(p):
            tiers.add("T2")
        elif _PLATFORM.match(p):
            tiers.add("T3")
        else:
            tiers.add("UNKNOWN")

    # T3 platform: agent non-draft PR fails outright (the refusal-by-policy check).
    if "T3" in tiers and author == AGENT and not is_draft:
        return {"status": "fail", "tier": "T3", "auto_merge": False,
                "require_review": True,
                "reason": "platform path: agent PR must be a draft (T3)"}

    # Strictest wins. UNKNOWN and any mix beyond a single clean T1 -> review, no auto-merge.
    only_t1 = tiers == {"T1"}
    if only_t1:
        apps = {_app_of(p) for p in paths}
        path_scoped = len(apps) == 1 and None not in apps
        keys_ok = changed_keys is not None and set(changed_keys).issubset(RUNTIME_KEY_ALLOWLIST)
        auto = bool(schema_valid and path_scoped and keys_ok)
        return {"status": "pass", "tier": "T1", "auto_merge": auto,
                "require_review": not auto,
                "reason": "runtime path"
                          + ("" if auto else ": not auto-mergeable ("
                             + ("schema invalid; " if not schema_valid else "")
                             + ("not path-scoped; " if not path_scoped else "")
                             + ("diff-shape not allowlisted; " if not keys_ok else "")
                             + "review required)")}

    if "UNKNOWN" in tiers:
        return {"status": "pass", "tier": "UNKNOWN", "auto_merge": False,
                "require_review": True,
                "reason": "path outside policy (fail-closed): review required"}

    # T2 present, or a T1+T2 mix, or draft T3 -> reviewable, no auto-merge, green status.
    tier = "T3" if "T3" in tiers else "T2"
    return {"status": "pass", "tier": tier, "auto_merge": False,
            "require_review": True,
            "reason": f"{tier}: human review required"}


def main() -> int:
    pr = json.load(sys.stdin) if not sys.stdin.isatty() else json.loads(sys.argv[1])
    v = classify(pr["author"], pr["paths"], pr.get("is_draft", False),
                 pr.get("changed_keys"), pr.get("schema_valid", True))
    print(json.dumps(v))
    return 0 if v["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
