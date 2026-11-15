#!/usr/bin/env python3
"""merge-guard: catch agent merges that happened outside policy, and undo them
(PLAN-hardening item 15, compensating control).

WHAT THIS IS, AND IS NOT. It is DETECTIVE and CORRECTIVE, not preventive. The
manifests repo is user-owned, and GitHub only offers "restrict who can merge" on
organisation repos, so nothing stops the agent's App from pressing merge on a pull
request that branch protection already considers mergeable (for example a T1 PR the
operator approved). This guard runs AFTER such a merge, raises the alarm and opens a
revert. Between the merge and the revert, the change is on main and Argo CD may sync
it. The preventive control stays BLOCKED until the repo moves to an organisation.

When it acts. For every pull request merged into main that it is shown:

  * merged BY the agent's App (`sre-agent-…[bot]`) — on any PR. The only identities
    allowed to merge are the auto-merge workflow (`github-actions[bot]`) and a human;
  * an agent PR (author = the App, or head branch `sre-agent/…`) merged without the
    required approvals: no approving review on the final head commit from someone
    other than the author and the App; or, when main's classifier says review is
    required (anything but an auto-mergeable T1), no approval from a code owner; or
    merged by `github-actions[bot]` when the classifier says it was NOT auto-mergeable;
    or a required check (REQUIRED_CHECKS, default `path-policy`) not green on that
    head commit.

Never on non-agent merges: a PR that the agent neither authored nor merged is
skipped without being examined further.

What it does about one:
  (a) opens an issue labelled `security` (one per PR, found again by a marker so a
      second run never duplicates it), and posts to Slack if SLACK_WEBHOOK_URL is set
      (optional; the URL is never printed);
  (b) builds the revert as one commit through the Git Data API (blobs, tree, commit,
      ref) on `merge-guard/revert-pr-<N>`, restoring each file the merge changed to
      its content just before the merge. If main has changed any of those files since
      the merge, it does NOT guess: no revert, the issue says a human is needed;
  (c) opens the revert PR, asks the code owners to review it, and tries to merge it
      with the workflow's GITHUB_TOKEN. Expect that merge to be refused under the
      current branch protection: GitHub does not start workflows for events made
      with GITHUB_TOKEN, so `path-policy` never runs on the revert PR, and the bot
      can't approve its own PR. When refused, the revert PR is left open, the issue
      gets the exact refusal, and this job fails red (::error). The revert must pass
      path-policy like any other change; the classifier's verdict on it is put in
      the PR body.

Known limits: the revert covers what the merge commit changed against its first
parent, which is the whole PR for squash and merge commits (auto-merge squashes)
but only the last commit of a multi-commit REBASE merge. And a merged PR that edits
.github/workflows/merge-guard.yml can change what runs after it (see the workflow).

Exit 0 = nothing out of policy; 1 = something was (the run goes red on purpose).

Inputs (env, set by merge-guard.yml): REPO, EVENT_NAME (push | pull_request |
schedule | workflow_dispatch), BEFORE/AFTER (push), PR_NUMBER (pull_request),
DEFAULT_BRANCH, GH_TOKEN, SRE_AGENT_LOGIN, REQUIRED_CHECKS, SWEEP_HOURS,
SLACK_WEBHOOK_URL. CODEOWNERS is read from the checkout this script came from.
Prints `guard: {json}` as its last line.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checks"))
import diff_shape    # noqa: E402
import path_policy   # noqa: E402

AGENT = os.environ.get("SRE_AGENT_LOGIN") or path_policy.AGENT
ACTIONS = "github-actions[bot]"
ZERO = "0" * 40
NOTE = ("merge-guard is detective and corrective, not preventive: GitHub allowed this merge "
        "(the repo is user-owned, so who may merge can't be restricted); this guard found it "
        "afterwards and is undoing it.")


class GHError(Exception):
    pass


def gh(path, method="GET", body=None, raw=False, paginate=False, ok404=False):
    args = ["gh", "api", "-X", method, path]
    if raw:
        args += ["-H", "Accept: application/vnd.github.raw"]
    if paginate:
        args.append("--paginate")
    if body is not None:
        args += ["--input", "-"]
    r = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        msg = " ".join((r.stdout.strip() + " " + r.stderr.strip()).split())[:400]
        if ok404 and ("404" in msg or "Not Found" in msg):
            return None
        raise GHError(msg)
    if raw:
        return r.stdout
    text, out, dec, i = r.stdout.strip(), [], json.JSONDecoder(), 0
    if not text:
        return None
    vals = []
    while i < len(text):
        v, i = dec.raw_decode(text, i)
        vals.append(v)
        while i < len(text) and text[i].isspace():
            i += 1
    if len(vals) == 1:
        return vals[0]
    for v in vals:
        out.extend(v if isinstance(v, list) else [v])
    return out


def content(repo, path, ref):
    return gh(f"repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}?ref={ref}",
              raw=True, ok404=True)


def code_owners(path):
    """Every @user named in CODEOWNERS (teams are skipped: approvals are per user)."""
    owners = set()
    try:
        for line in open(path):
            for tok in line.split("#", 1)[0].split()[1:]:
                if tok.startswith("@") and "/" not in tok:
                    owners.add(tok[1:])
    except OSError:
        pass
    return owners


# --------------------------------------------------------- which PRs ---
def prs_for_push(repo, before, after):
    if not after:
        return []
    shas = [after] if (not before or before == ZERO) else \
        [c["sha"] for c in (gh(f"repos/{repo}/compare/{before}...{after}") or {}).get("commits") or []]
    nums = []
    for sha in shas:
        for p in gh(f"repos/{repo}/commits/{sha}/pulls") or []:
            if p.get("merged_at") and p["number"] not in nums:
                nums.append(p["number"])
    return nums


def prs_for_sweep(repo, base, hours):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    nums = []
    for p in gh(f"repos/{repo}/pulls?state=closed&base={base}&sort=updated&direction=desc&per_page=50") or []:
        m = p.get("merged_at")
        if m and dt.datetime.fromisoformat(m.replace("Z", "+00:00")) >= since:
            nums.append(p["number"])
    return nums


# ------------------------------------------------------------ evaluate ---
def landed(repo, pr):
    """(merge commit, its first parent, [file dicts]) — the change as it landed on main."""
    m = pr.get("merge_commit_sha")
    c = gh(f"repos/{repo}/commits/{m}")
    parent = c["parents"][0]["sha"]
    files = (gh(f"repos/{repo}/compare/{parent}...{m}") or {}).get("files") or []
    return m, parent, files


def evaluate(repo, pr, owners, required_checks):
    """[] when the merge is within policy (or not the agent's business), else reasons."""
    n = pr["number"]
    author = pr["user"]["login"]
    merged_by = (pr.get("merged_by") or {}).get("login") or "?"
    agent_pr = author == AGENT or pr["head"]["ref"].startswith("sre-agent/")
    app_merge = merged_by == AGENT
    if not agent_pr and not app_merge:
        return None, f"#{n}: not an agent PR and not merged by the agent ({author}, merged by {merged_by}); ignored"
    reasons = []
    if app_merge:
        reasons.append(f"merged by the agent's App identity {AGENT}; only the auto-merge workflow "
                       f"({ACTIONS}) or a human may merge")
    if agent_pr:
        head = pr["head"]["sha"]
        latest = {}
        for r in gh(f"repos/{repo}/pulls/{n}/reviews", paginate=True) or []:
            if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[(r.get("user") or {}).get("login")] = r
        approvers = sorted(l for l, r in latest.items()
                           if r["state"] == "APPROVED" and r.get("commit_id") == head
                           and l not in (author, AGENT, None))
        humans = [a for a in approvers if not a.endswith("[bot]")]
        _, parent, files = landed(repo, pr)
        m = pr["merge_commit_sha"]
        paths = sorted({f["filename"] for f in files} |
                       {f["previous_filename"] for f in files if f.get("previous_filename")})
        keys = set()
        for p in paths:
            keys.update(diff_shape.changed_keys(content(repo, p, parent) or "", content(repo, p, m) or ""))
        v = path_policy.classify(author, paths, False, changed_keys=sorted(keys),
                                 schema_valid=diff_shape.UNKNOWN not in keys)
        if not approvers:
            reasons.append(f"no approving review on its final head commit {head[:8]} from anyone but "
                           f"the author or the App")
        elif v["require_review"]:
            need = owners or None
            ok = [h for h in humans if need is None or h in need]
            if not ok:
                reasons.append(f"main's classifier says {v['tier']}, review required ({v['reason']}), "
                               f"but no code owner ({', '.join(sorted(owners)) or 'any human'}) approved it; "
                               f"approvals: {', '.join(approvers)}")
        if merged_by == ACTIONS and not v["auto_merge"]:
            reasons.append(f"merged by {ACTIONS} (the auto-merge identity) but main's classifier says it "
                           f"is not auto-mergeable ({v['reason']})")
        runs = (gh(f"repos/{repo}/commits/{head}/check-runs") or {}).get("check_runs") or []
        for name in required_checks:
            got = [r for r in runs if r.get("name") == name]
            concl = (got[-1].get("conclusion") if got else None) or "missing"
            if concl != "success":
                reasons.append(f"required check `{name}` was {concl} on head {head[:8]}")
    return reasons, f"#{n}: author {author}, merged by {merged_by}"


# --------------------------------------------------------------- alert ---
def marker(n):
    return f"<!-- merge-guard pr={n} -->"


def find_issue(repo, n):
    for i in gh(f"repos/{repo}/issues?labels=security&state=all&per_page=100", paginate=True) or []:
        if marker(n) in (i.get("body") or ""):
            return i
    return None


def open_issue(repo, pr, reasons):
    try:
        gh(f"repos/{repo}/labels", "POST", {"name": "security", "color": "b60205",
                                             "description": "security alerts (merge-guard)"})
    except GHError:
        pass   # it exists already
    n = pr["number"]
    body = "\n".join([
        marker(n),
        f"**An agent pull request was merged outside policy: #{n}**", "",
        f"- PR: {pr.get('html_url') or '#' + str(n)} — {pr.get('title', '')}",
        f"- author: `{pr['user']['login']}`, merged by: `{(pr.get('merged_by') or {}).get('login')}`, "
        f"at {pr.get('merged_at')}",
        f"- merge commit: `{pr.get('merge_commit_sha')}`", "",
        "**Why this is out of policy:**", *[f"- {r}" for r in reasons], "",
        f"_{NOTE}_", "",
        "The revert attempt is reported in a comment below."])
    return gh(f"repos/{repo}/issues", "POST",
              {"title": f"security: agent PR #{n} merged outside policy", "body": body,
               "labels": ["security"]})


def slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return "skipped (no SLACK_WEBHOOK_URL secret)"
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return f"sent (HTTP {r.status})"
    except Exception as e:  # noqa: BLE001 — never let the webhook (or its URL) break the alert
        return f"failed ({type(e).__name__})"


# -------------------------------------------------------------- revert ---
def revert(repo, pr, base, owners):
    """Open (and try to merge) the revert. Returns a dict describing what happened."""
    n = pr["number"]
    branch = f"merge-guard/revert-pr-{n}"
    have = gh(f"repos/{repo}/git/ref/heads/{branch}", ok404=True)
    if have:
        prs = gh(f"repos/{repo}/pulls?state=all&head={repo.split('/')[0]}:{branch}") or []
        return {"opened": False, "existing": prs[0]["number"] if prs else None, "branch": branch,
                "detail": f"a revert branch {branch} already exists"
                          + (f" (PR #{prs[0]['number']})" if prs else "")}
    m, parent, files = landed(repo, pr)
    tip = gh(f"repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
    restore, moved = {}, []
    for f in files:
        targets = [f["filename"]] + ([f["previous_filename"]] if f.get("previous_filename") else [])
        for p in targets:
            at_m, at_tip = content(repo, p, m), content(repo, p, tip)
            if at_m != at_tip:
                moved.append(p)
            restore[p] = (at_m, content(repo, p, parent))
    if moved:
        return {"opened": False, "branch": branch,
                "detail": f"main has changed {', '.join(sorted(moved))} since the merge, so a plain revert "
                          f"would undo later work. Not guessing: a human has to revert this one."}
    keys = set()
    for p, (now, old) in restore.items():
        keys.update(diff_shape.changed_keys(now or "", old or ""))
    paths = sorted(restore)
    v = path_policy.classify(ACTIONS, paths, False, changed_keys=sorted(keys),
                             schema_valid=diff_shape.UNKNOWN not in keys)
    tree_base = gh(f"repos/{repo}/commits/{tip}")["commit"]["tree"]["sha"]
    entries = []
    for p in paths:
        old = restore[p][1]
        if old is None:
            entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})
        else:
            b = gh(f"repos/{repo}/git/blobs", "POST", {"content": old, "encoding": "utf-8"})
            entries.append({"path": p, "mode": "100644", "type": "blob", "sha": b["sha"]})
    tree = gh(f"repos/{repo}/git/trees", "POST", {"base_tree": tree_base, "tree": entries})
    msg = f"revert #{n}: merged outside policy (merge-guard)\n\nReverts {m}."
    commit = gh(f"repos/{repo}/git/commits", "POST", {"message": msg, "tree": tree["sha"], "parents": [tip]})
    gh(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    body = "\n".join([
        f"Reverts #{n} (merge commit `{m[:12]}`), which was merged outside policy. Opened by merge-guard.", "",
        "Files restored to their content just before the merge:", *[f"- `{p}`" for p in paths], "",
        f"**path-policy (main's classifier) on this revert:** {v['status']}, {v['tier']} — {v['reason']}", "",
        "This revert must pass the required checks like any other change. GitHub does not start "
        "workflows for pull requests opened with GITHUB_TOKEN, so if path-policy shows as expected "
        "but not run: close and reopen this PR (that starts it), then approve.", "",
        f"_{NOTE}_"])
    new = gh(f"repos/{repo}/pulls", "POST",
             {"title": f"revert #{n} (merged outside policy)", "head": branch, "base": base, "body": body})
    num = new["number"]
    asks = sorted(o for o in owners if o != ACTIONS)
    if asks:
        try:
            gh(f"repos/{repo}/pulls/{num}/requested_reviewers", "POST", {"reviewers": asks})
        except GHError:
            pass
    try:
        r = gh(f"repos/{repo}/pulls/{num}/merge", "PUT",
               {"merge_method": "squash", "sha": commit["sha"],
                "commit_title": f"revert #{n} (merged outside policy) (#{num})"})
        merged = bool((r or {}).get("merged"))
        why = "" if merged else json.dumps(r)[:300]
    except GHError as e:
        merged, why = False, str(e)
    return {"opened": True, "pr": num, "branch": branch, "merged": merged, "refused": why,
            "classifier": v, "paths": paths,
            "detail": (f"revert PR #{num} opened and merged" if merged else
                       f"revert PR #{num} opened but NOT merged — branch protection refused the bot: {why}. "
                       f"It is left open for the operator.")}


# ---------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--codeowners", default=os.path.join(HERE, "..", "..", "CODEOWNERS"))
    a = ap.parse_args()
    repo = os.environ["REPO"]
    event = os.environ.get("EVENT_NAME", "")
    base = os.environ.get("DEFAULT_BRANCH") or "main"
    checks = [c.strip() for c in (os.environ.get("REQUIRED_CHECKS") or "path-policy").split(",") if c.strip()]
    owners = code_owners(a.codeowners)
    print("merge-guard: " + NOTE)
    if event == "push":
        nums = prs_for_push(repo, os.environ.get("BEFORE", ""), os.environ.get("AFTER", ""))
    elif event == "pull_request":
        nums = [int(os.environ["PR_NUMBER"])] if os.environ.get("PR_MERGED") == "true" else []
        if not nums:
            print("pull request closed without merging; nothing to check")
    else:
        nums = prs_for_sweep(repo, base, float(os.environ.get("SWEEP_HOURS") or 24))
    summary = {"event": event, "checked": [], "ignored": [], "violations": []}
    for n in nums:
        pr = gh(f"repos/{repo}/pulls/{n}")
        if not pr.get("merged") or pr["base"]["ref"] != base:
            continue
        reasons, who = evaluate(repo, pr, owners, checks)
        print(who)
        if reasons is None:
            summary["ignored"].append(n)
            continue
        summary["checked"].append(n)
        if not reasons:
            print("  within policy")
            continue
        for r in reasons:
            print(f"::error::agent PR #{n} merged outside policy: {r}")
        v = {"pr": n, "reasons": reasons}
        issue = find_issue(repo, n)
        if issue:
            v["issue"] = issue["number"]
            v["issue_new"] = False
        else:
            issue = open_issue(repo, pr, reasons)
            v["issue"], v["issue_new"] = issue["number"], True
            v["slack"] = slack(f":rotating_light: agent PR #{n} on {repo} was merged outside policy "
                               f"({reasons[0]}). merge-guard opened issue #{issue['number']} and is reverting it.")
        try:
            rv = revert(repo, pr, base, owners)
        except GHError as e:
            rv = {"opened": False, "detail": f"the revert could not be built: {e}"}
        v["revert"] = {k: rv.get(k) for k in ("opened", "pr", "merged", "existing", "detail")}
        if rv.get("opened") or v["issue_new"]:   # a re-run never repeats the comment
            gh(f"repos/{repo}/issues/{issue['number']}/comments", "POST",
               {"body": f"{marker(n)}\n**Revert:** {rv['detail']}"})
        if not rv.get("merged"):
            print(f"::error::revert of #{n} not merged: {rv['detail']}")
        print(f"  {rv['detail']}")
        summary["violations"].append(v)
    print("guard: " + json.dumps(summary))
    return 1 if summary["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
