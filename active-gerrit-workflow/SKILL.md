---
name: active-gerrit-workflow
description: "Use this skill for business-level Gerrit Code Review workflows: review queue triage, review briefs, pre-submit checks, release branch readiness, multi-change coordination, applying team review policy, or producing workflow reports that reuse active-gerrit."
---

# Active Gerrit Workflow

## Purpose

`active-gerrit-workflow` is the business workflow layer for Gerrit Code Review. It combines low-level Gerrit data from `active-gerrit` with team policies, review checklists, release rules, risk scoring, and multi-change reporting.

Do not reimplement Gerrit authentication, XSSI cleanup, REST endpoint wrappers, or generic client behavior here. Call or follow `active-gerrit` for low-level Gerrit operations.

## Default Workflow

1. Classify the user's request as a workflow goal, such as review queue triage, review brief generation, pre-submit readiness, release readiness, or cross-change coordination.
2. Gather Gerrit facts through the installed `active-gerrit` command or its documented result schemas.
3. Apply the relevant business policy from `references/` only after the low-level Gerrit data is known.
4. Produce a workflow report with conclusions, evidence, risks, and recommended next actions.
5. For unsupported low-level Gerrit operations, fall back to `active-gerrit` instead of creating a parallel REST client.

## Runtime Configuration

- Prefer `active-gerrit-workflow ...` and `active-gerrit ...` launchers for normal Agent use; they load the runtime env file before execution.
- Do not direct Agents to bypass launchers with `python scripts/workflow_cli.py ...` or `python ../active-gerrit/scripts/gerrit_cli.py ...` unless developing or testing this repository.
- If a direct Python invocation is unavoidable, expect the low-level `active-gerrit` CLI to self-load `$ACTIVE_GERRIT_WORKFLOW_ENV_FILE` or `~/.config/active-gerrit-workflow/env` for missing Gerrit settings, while preserving already-set environment variables.

## References

- Read `references/business-workflows.md` for review queue, review brief, pre-submit, release, and multi-change workflow templates.
- Read `references/review-policies.md` for team review rules, checklist expectations, vote policy, and risk classification.
- Read `references/release-policies.md` when the branch, submit target, or rollout risk suggests release, stable, or hotfix handling.
- Read `references/escalation-rules.md` when owner, reviewer, visibility, or blocker escalation paths are unclear.

## Safety Rules

- Business workflows must inherit `active-gerrit` write-operation protections.
- Keep dry-run behavior as the default for bulk comments, votes, submit, abandon, rebase, reviewer reassignment, and release-affecting actions.
- Clearly separate facts from policy judgment in reports.
- Never include Gerrit credentials, cookies, tokens, or authorization headers in generated reports or logs.

## Resource Layout

- `scripts/` will hold workflow orchestration and report helpers.
- `references/` will hold business workflow and policy material loaded only when needed.
- `agents/openai.yaml` holds UI metadata for the skill.
