# ai-sre-manifests

GitOps manifests for the AI SRE platform. **ArgoCD is the cluster's only writer;
the agent's only write surface is pull requests on this repo.**

## Path policy (= the autonomy model)

| Path | Merge policy | Tier |
|---|---|---|
| `apps/*/runtime/` | Auto-merge on green checks (schema-valid, path-scoped, diff-shape allowlisted) | T1 |
| `apps/*/config/` | Human review required (CODEOWNERS) | T2 |
| `platform/` | Agent PRs must be drafts (status check fails non-draft agent PRs) | T3 |

Enforced by branch protection + `CODEOWNERS` + the `path-policy` required status
check (`.github/workflows/path-policy.yml` → `scripts/checks/path_policy.py`).
The agent (`sre-agent[bot]`) can never merge its own reviewed-path PR.
