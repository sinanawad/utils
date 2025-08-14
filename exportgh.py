#!/usr/bin/env python3
"""exportgh.py

Developer productivity/statistics exporter for GitHub activity.

Authentication: Reuses the same GitHub CLI token extraction approach as `lp2gh.py`.

Inputs (CLI):
  --user <github_username> (required)
  --from YYYY-MM-DD (inclusive start date; default: 30 days ago)
  --to   YYYY-MM-DD (inclusive end date; default: today)
  --days N  (alternative to --from/--to: last N days)
  --org <org> (filter authored/ reviewed PRs to repos under this org only; can repeat)
  --repo <full_name> (additional repo filter; can repeat)
  --max-prs N (cap number of PRs to examine in detail; default 300)
  --json (emit JSON in addition to human-readable summary)
  --deep (perform deeper per-PR API calls to compute review metrics: lines added/deleted, time to first review, classification; more API cost)
  --token <token> (optional; if missing extract from gh CLI)
  --rate-limit-warn REM (warn if remaining core limit below this; default 100)
  --jira-pattern REGEX (override default Jira ticket regex)
  --csv FILE (optional: write per-PR breakdown to CSV)

Statistics produced (authored PRs in window):
  - count_opened, count_merged, merge_rate
  - count_closed (closed without merge)
  - median/min/max/p90 time_to_close (h), same for time_to_merge
  - median time_to_first_review (if --deep)
  - code churn totals & averages (additions, deletions, total, net)
  - distribution by conventional commit type (feat, fix, chore, docs, refactor, test, perf, build, ci, style, revert, other)
  - classification counts: bug_fixes, jira_tickets, unsolicited
  - authored_prs_per_day

Reviewed PRs in window:
  - count_reviewed (distinct PRs with at least one review by user)
  - review_to_authored_ratio

Potential further KPIs (printed if computable):
  - avg_comments_per_authored_pr
  - avg_reviews_received_per_authored_pr

Limitations:
  - Public activity only unless token has access; private repos only included if token grants it.
  - Review stats rely on search 'reviewed-by:' which finds PRs with at least one submitted review; does not count simple comment-only participation.
  - Time to first review requires extra API calls (enabled with --deep).

"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple
import subprocess

try:
	from github import Github, GithubException
except ImportError:  # Provide a clear instruction
	print("Missing dependency 'PyGithub'. Install with: pip install PyGithub", file=sys.stderr)
	raise


JIRA_RE_DEFAULT = r"[A-Z]{2,10}-\d+"
CONVENTIONAL_TYPES = [
	"feat",
	"fix",
	"chore",
	"docs",
	"refactor",
	"test",
	"perf",
	"build",
	"ci",
	"style",
	"revert",
]


@dataclass
class PRStats:
	number: int
	repo: str
	title: str
	state: str
	created_at: dt.datetime
	closed_at: Optional[dt.datetime]
	merged_at: Optional[dt.datetime]
	additions: Optional[int] = None
	deletions: Optional[int] = None
	changed_files: Optional[int] = None
	author_association: Optional[str] = None
	comment_count: Optional[int] = None
	review_count: Optional[int] = None
	first_review_at: Optional[dt.datetime] = None
	conventional_type: str = "other"
	is_bug_fix: bool = False
	has_jira: bool = False
	is_unsolicited: bool = False

	def to_row(self):
		return {
			**asdict(self),
			"time_to_close_h": hours_delta(self.created_at, self.closed_at),
			"time_to_merge_h": hours_delta(self.created_at, self.merged_at),
			"time_to_first_review_h": hours_delta(self.created_at, self.first_review_at),
			"total_changes": (self.additions or 0) + (self.deletions or 0) if self.additions is not None else None,
			"net_additions": (self.additions - self.deletions) if self.additions is not None and self.deletions is not None else None,
		}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
	today = dt.date.today()
	default_from = today - dt.timedelta(days=30)
	p = argparse.ArgumentParser(description="Export GitHub developer statistics.")
	p.add_argument("--user", required=True, help="GitHub username to analyze")
	p.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD (inclusive)")
	p.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD (inclusive)")
	p.add_argument("--days", type=int, help="Alternative window: last N days (overrides --from/--to)")
	p.add_argument("--org", action="append", help="Limit to PRs in these organizations (repeatable)")
	p.add_argument("--repo", action="append", help="Additional explicit repos full_name (owner/name)")
	p.add_argument("--max-prs", type=int, default=300, help="Max authored PRs to inspect in detail")
	p.add_argument("--json", action="store_true", help="Emit JSON output")
	p.add_argument("--deep", action="store_true", help="Fetch per-PR details (lines, first review time). More API calls.")
	p.add_argument("--token", help="GitHub token (if omitted, extracted from gh CLI)")
	p.add_argument("--rate-limit-warn", type=int, default=100, help="Warn if core remaining below this")
	p.add_argument("--jira-pattern", default=JIRA_RE_DEFAULT, help="Regex to detect Jira tickets (default: %(default)s)")
	p.add_argument("--csv", help="Write per-PR breakdown to CSV file")
	return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> Tuple[dt.datetime, dt.datetime]:
	if args.days:
		end = dt.datetime.combine(dt.date.today(), dt.time.max)
		start = end - dt.timedelta(days=args.days)
	else:
		date_from = dt.datetime.strptime(args.date_from, "%Y-%m-%d") if args.date_from else dt.datetime.combine(dt.date.today() - dt.timedelta(days=30), dt.time.min)
		date_to = dt.datetime.strptime(args.date_to, "%Y-%m-%d") if args.date_to else dt.datetime.combine(dt.date.today(), dt.time.max)
		start = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
		end = date_to.replace(hour=23, minute=59, second=59, microsecond=999999)
	return start, end


def gh_get_token_from_cli() -> Tuple[str, str]:
	"""Replicates token acquisition style from lp2gh.py using gh CLI."""
	result = subprocess.run(["gh", "auth", "status", "--show-token"], capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError("Failed to get GitHub token via gh CLI. Run 'gh auth login'.")
	token_line = next(line for line in result.stdout.splitlines() if 'Token:' in line)
	token = token_line.split('Token:')[1].strip()
	acct_line = next(line for line in result.stdout.splitlines() if 'account ' in line)
	account = acct_line.split('account ')[1].strip().split()[0]
	return token, account


def hours_delta(start: dt.datetime, end: Optional[dt.datetime]) -> Optional[float]:
	if not end:
		return None
	return round((end - start).total_seconds() / 3600.0, 2)


def utcnow() -> dt.datetime:
	"""Return timezone-aware current UTC datetime (replacement for deprecated utcnow)."""
	return dt.datetime.now(dt.timezone.utc)


def percentile(data: List[float], p: float) -> Optional[float]:
	if not data:
		return None
	data_sorted = sorted(data)
	k = (len(data_sorted) - 1) * p
	f = int(k)
	c = min(f + 1, len(data_sorted) - 1)
	if f == c:
		return round(data_sorted[int(k)], 2)
	d0 = data_sorted[f] * (c - k)
	d1 = data_sorted[c] * (k - f)
	return round(d0 + d1, 2)


def conventional_type_from_title(title: str) -> str:
	m = re.match(r"^(\w+)(?:\([^)]*\))?!?:", title)
	if m:
		t = m.group(1).lower()
		if t in CONVENTIONAL_TYPES:
			return t
	return "other"


def classify_pr(title: str, body: Optional[str], comments_text: str, jira_re: re.Pattern) -> Tuple[bool, bool, bool, str]:
	lower = (title + " " + (body or "")).lower()
	has_jira = bool(jira_re.search(title)) or bool(jira_re.search(body or "")) or bool(jira_re.search(comments_text))
	is_bug = 'bug' in lower or conventional_type_from_title(title) == 'fix'
	# unsolicited: neither Jira nor bug
	unsolicited = not has_jira and not is_bug
	ctype = conventional_type_from_title(title)
	return is_bug, has_jira, unsolicited, ctype


def _search_issues_any(gh: Github, query: str, sort: Optional[str] = None, order: Optional[str] = None):
	"""Compatibility wrapper: PyGithub renamed search_issues to search_issues_and_pull_requests in newer versions.

	Use whichever exists so script works across versions.
	"""
	if hasattr(gh, "search_issues_and_pull_requests"):
		return gh.search_issues_and_pull_requests(query, sort=sort, order=order)
	# Fallback for older versions (like 2.7.0) where only search_issues exists
	return gh.search_issues(query, sort=sort, order=order)


def search_authored_prs(gh: Github, username: str, start: dt.datetime, end: dt.datetime, org_filters: List[str] | None, repo_filters: List[str] | None, max_items: int) -> List:
	date_range = f"{start.date()}..{end.date()}"
	base_q = f"type:pr author:{username} created:{date_range}"
	qualifiers = []
	if org_filters:
		qualifiers.append("(" + " ".join(f"org:{o}" for o in org_filters) + ")")
	if repo_filters:
		qualifiers.append("(" + " ".join(f"repo:{r}" for r in repo_filters) + ")")
	q = base_q + (" " + " ".join(qualifiers) if qualifiers else "")
	results = _search_issues_any(gh, q, sort="created", order="asc")
	items = []
	for i, issue in enumerate(results):
		if i >= max_items:
			break
		if not issue.pull_request:  # sanity
			continue
		items.append(issue)
	return items


def search_reviewed_prs(gh: Github, username: str, start: dt.datetime, end: dt.datetime, org_filters: List[str] | None, repo_filters: List[str] | None, max_items: int) -> List:
	date_range = f"{start.date()}..{end.date()}"
	base_q = f"type:pr reviewed-by:{username} updated:{date_range}"
	qualifiers = []
	if org_filters:
		qualifiers.append("(" + " ".join(f"org:{o}" for o in org_filters) + ")")
	if repo_filters:
		qualifiers.append("(" + " ".join(f"repo:{r}" for r in repo_filters) + ")")
	q = base_q + (" " + " ".join(qualifiers) if qualifiers else "")
	results = _search_issues_any(gh, q, sort="updated", order="asc")
	items = []
	for i, issue in enumerate(results):
		if i >= max_items:
			break
		if not issue.pull_request:
			continue
		items.append(issue)
	return items


def fetch_pr_detail(gh: Github, issue) -> Optional[PRStats]:
	try:
		repo_full = issue.repository.full_name
		repo = gh.get_repo(repo_full)
		pr = repo.get_pull(issue.number)
	except GithubException as e:
		print(f"WARN: Failed to fetch PR detail {issue.repository.full_name}#{issue.number}: {e}", file=sys.stderr)
		return None
	return PRStats(
		number=pr.number,
		repo=repo_full,
		title=pr.title,
		state=pr.state,
		created_at=pr.created_at,
		closed_at=pr.closed_at,
		merged_at=pr.merged_at,
		additions=pr.additions,
		deletions=pr.deletions,
		changed_files=pr.changed_files,
		author_association=pr.author_association,
		comment_count=pr.comments,
		review_count=pr.review_comments,
	)


def enrich_deep(pr_stat: PRStats, pr, jira_re: re.Pattern):
	# Gather comments text for classification & first review time
	comments_text_parts = []
	first_review_time = None
	try:
		for review in pr.get_reviews():
			if not first_review_time:
				first_review_time = review.submitted_at
		for c in pr.get_issue_comments():
			comments_text_parts.append(c.body or "")
	except GithubException:
		pass
	comments_text = "\n".join(comments_text_parts)
	is_bug, has_jira, unsolicited, ctype = classify_pr(pr.title, pr.body, comments_text, jira_re)
	pr_stat.is_bug_fix = is_bug
	pr_stat.has_jira = has_jira
	pr_stat.is_unsolicited = unsolicited
	pr_stat.conventional_type = ctype
	pr_stat.first_review_at = first_review_time


def aggregate(pr_stats: List[PRStats], reviewed_ids: set) -> Dict:
	authored = len(pr_stats)
	merged = sum(1 for p in pr_stats if p.merged_at)
	closed = sum(1 for p in pr_stats if p.closed_at and not p.merged_at)
	time_to_close = [hours_delta(p.created_at, p.closed_at) for p in pr_stats if p.closed_at]
	time_to_merge = [hours_delta(p.created_at, p.merged_at) for p in pr_stats if p.merged_at]
	time_to_first_review = [hours_delta(p.created_at, p.first_review_at) for p in pr_stats if p.first_review_at]
	additions = [p.additions for p in pr_stats if p.additions is not None]
	deletions = [p.deletions for p in pr_stats if p.deletions is not None]
	total_changes = [(p.additions or 0) + (p.deletions or 0) for p in pr_stats if p.additions is not None]
	net_adds = [p.additions - p.deletions for p in pr_stats if p.additions is not None and p.deletions is not None]
	by_type = {}
	bug_fixes = sum(1 for p in pr_stats if p.is_bug_fix)
	jira = sum(1 for p in pr_stats if p.has_jira)
	unsolicited = sum(1 for p in pr_stats if p.is_unsolicited)
	for p in pr_stats:
		by_type[p.conventional_type] = by_type.get(p.conventional_type, 0) + 1
	reviewed_count = len(reviewed_ids - { (p.repo, p.number) for p in pr_stats }) + len({ (p.repo, p.number) for p in pr_stats if (p.repo, p.number) in reviewed_ids })
	# Derived stats
	agg = {
		"authored_prs": authored,
		"merged_prs": merged,
		"closed_prs": closed,
		"merge_rate": round(merged / authored, 2) if authored else 0,
		"reviewed_prs": reviewed_count,
		"review_to_authored_ratio": round(reviewed_count / authored, 2) if authored else None,
		"time_to_close_h": summarize_dist(time_to_close),
		"time_to_merge_h": summarize_dist(time_to_merge),
		"time_to_first_review_h": summarize_dist(time_to_first_review) if time_to_first_review else None,
		"additions_total": sum(additions) if additions else 0,
		"deletions_total": sum(deletions) if deletions else 0,
		"total_changes": sum(total_changes) if total_changes else 0,
		"net_additions_total": sum(net_adds) if net_adds else 0,
		"avg_additions": round(sum(additions)/len(additions),2) if additions else 0,
		"avg_deletions": round(sum(deletions)/len(deletions),2) if deletions else 0,
		"avg_total_changes": round(sum(total_changes)/len(total_changes),2) if total_changes else 0,
		"avg_net_additions": round(sum(net_adds)/len(net_adds),2) if net_adds else 0,
		"by_conventional_type": by_type,
		"bug_fix_prs": bug_fixes,
		"jira_prs": jira,
		"unsolicited_prs": unsolicited,
	}
	# Per-day authored rate
	if pr_stats:
		span_days = max(1, (max(p.created_at for p in pr_stats) - min(p.created_at for p in pr_stats)).days + 1)
		agg["authored_prs_per_day"] = round(authored / span_days, 2)
	return agg


def summarize_dist(values: List[float]) -> Dict[str, float]:
	if not values:
		return {}
	return {
		"count": len(values),
		"median": round(statistics.median(values), 2),
		"p90": percentile(values, 0.9),
		"min": round(min(values), 2),
		"max": round(max(values), 2),
		"avg": round(sum(values)/len(values), 2),
	}


def rate_limit_notice(gh: Github, warn_threshold: int):
	try:
		rl = gh.get_rate_limit().core
		if rl.remaining < warn_threshold:
			print(f"WARN: Low rate limit remaining {rl.remaining}/{rl.limit}, resets at {rl.reset}" , file=sys.stderr)
	except Exception:
		pass


def write_csv(path: str, pr_stats: List[PRStats]):
	fieldnames = list(pr_stats[0].to_row().keys()) if pr_stats else []
	with open(path, 'w', newline='') as f:
		w = csv.DictWriter(f, fieldnames=fieldnames)
		w.writeheader()
		for p in pr_stats:
			w.writerow(p.to_row())
	print(f"Wrote CSV: {path}")


def human_summary(agg: Dict):
    """Print a human-readable summary with brief explanations for each metric."""
    explanations = {
        "authored_prs": "Total PRs opened by the user in the window",
        "merged_prs": "Authored PRs that were merged",
        "closed_prs": "Authored PRs closed without merge (abandoned / superseded)",
        "merge_rate": "merged_prs / authored_prs (success ratio)",
        "reviewed_prs": "Distinct PRs (by others) where user left at least one formal review",
        "review_to_authored_ratio": "reviewed_prs / authored_prs (participation as reviewer vs author)",
        "authored_prs_per_day": "Average authored PRs per active day span in window",
        "bug_fix_prs": "Authored PRs classified as bug fixes (title/body or 'fix:' type)",
        "jira_prs": "Authored PRs referencing a Jira ticket (matched regex)",
        "unsolicited_prs": "Authored PRs that are neither bug fix nor Jira-referenced",
        "additions_total": "Sum of lines added across authored PRs (deep mode)",
        "deletions_total": "Sum of lines deleted across authored PRs (deep mode)",
        "total_changes": "Total churn = additions + deletions (deep mode)",
        "net_additions_total": "Net lines added (additions - deletions) (deep mode)",
        "avg_additions": "Average lines added per authored PR (deep mode)",
        "avg_deletions": "Average lines deleted per authored PR (deep mode)",
        "avg_total_changes": "Average churn per authored PR (deep mode)",
        "avg_net_additions": "Average net lines added per authored PR (deep mode)",
    }
    dist_explanations = {
        "time_to_merge_h": "Hours from PR creation to merge",
        "time_to_close_h": "Hours from PR creation to close (merged or closed) where closed",
        "time_to_first_review_h": "Hours until first submitted review (deep mode)",
    }

    print("\n=== Developer GitHub Statistics ===")
    for k in [
        "authored_prs","merged_prs","closed_prs","merge_rate",
        "reviewed_prs","review_to_authored_ratio","authored_prs_per_day",
        "bug_fix_prs","jira_prs","unsolicited_prs"
    ]:
        if k in agg and agg[k] is not None:
            print(f"{k}: {agg[k]}  # {explanations.get(k,'')}")

    print("\nConventional Commit Types:  # Count of authored PRs by conventional prefix or 'other'")
    by_type = agg.get("by_conventional_type", {})
    for t, c in sorted(by_type.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {t}: {c}")

    def print_dist(name_key: str, dist):
        if dist:
            label = dist_explanations.get(name_key, "")
            print(f"\n{name_key} (hours):  # {label}")
            # show distribution metrics key=value
            print("  " + ", ".join(f"{k}={v}" for k,v in dist.items()))

    print_dist("time_to_merge_h", agg.get("time_to_merge_h"))
    print_dist("time_to_close_h", agg.get("time_to_close_h"))
    print_dist("time_to_first_review_h", agg.get("time_to_first_review_h"))

    print("\nCode Churn:  # Requires --deep to populate")
    for k in [
        "additions_total","deletions_total","total_changes","net_additions_total",
        "avg_additions","avg_deletions","avg_total_changes","avg_net_additions"
    ]:
        if k in agg:
            print(f"  {k}: {agg.get(k)}  # {explanations.get(k,'')}")


def main(argv: Optional[List[str]] = None):
	args = parse_args(argv)
	start, end = resolve_window(args)
	jira_re = re.compile(args.jira_pattern)
	if args.token:
		token = args.token
	else:
		try:
			token, account = gh_get_token_from_cli()
			print(f"Using token from gh CLI for account {account}", file=sys.stderr)
		except Exception as e:
			print(f"ERROR: {e}", file=sys.stderr)
			return 1
	gh = Github(token, per_page=100)
	rate_limit_notice(gh, args.rate_limit_warn)
	authored_issues = search_authored_prs(gh, args.user, start, end, args.org, args.repo, args.max_prs)
	reviewed_issues = search_reviewed_prs(gh, args.user, start, end, args.org, args.repo, args.max_prs)
	reviewed_ids = { (i.repository.full_name, i.number) for i in reviewed_issues }
	pr_stats: List[PRStats] = []
	for issue in authored_issues:
		detail = fetch_pr_detail(gh, issue)
		if not detail:
			continue
		if args.deep:
			try:
				repo = gh.get_repo(detail.repo)
				pr = repo.get_pull(detail.number)
				enrich_deep(detail, pr, jira_re)
			except GithubException:
				pass
		pr_stats.append(detail)
	agg = aggregate(pr_stats, reviewed_ids)
	human_summary(agg)
	if args.json:
		out = {
			"window": {"start": start.isoformat(), "end": end.isoformat()},
			"user": args.user,
			"aggregate": agg,
			"prs": [p.to_row() for p in pr_stats],
		}
		print("\nJSON:\n" + json.dumps(out, indent=2, default=str))
	if args.csv and pr_stats:
		write_csv(args.csv, pr_stats)
	return 0


if __name__ == "__main__":  # pragma: no cover
	sys.exit(main())
