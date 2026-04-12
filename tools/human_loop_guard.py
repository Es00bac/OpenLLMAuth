#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


STATE_FILE = Path(__file__).resolve().parent.parent / "docs" / "HUMAN_LOOP_STATE.json"

ORDER: List[str] = [
    "roundtable_open",
    "discussion_in_progress",
    "discussion_completed",
    "step_card_presented",
    "human_approved",
    "implementation_started",
    "tests_passed",
    "reported",
]

ALLOWED_NEXT: Dict[str, List[str]] = {
    "roundtable_open": ["discussion_in_progress"],
    "discussion_in_progress": ["discussion_completed"],
    "discussion_completed": ["step_card_presented"],
    "step_card_presented": ["human_approved", "discussion_in_progress"],
    "human_approved": ["implementation_started"],
    "implementation_started": ["tests_passed"],
    "tests_passed": ["reported"],
    "reported": ["roundtable_open"],
}

LEGACY_STATE_MAP: Dict[str, str] = {
    "roundtable_logged": "discussion_completed",
}

REQUIRED_PERSONAS: List[str] = [
    "The Core Architect",
    "The Auth Hacker",
    "The Cognitive Systems Engineer",
    "The Memory Archivist",
    "The Sandbox Guardian",
    "The Security Auditor",
    "The Telemetry Analyst",
    "The Dashboard Weaver",
    "The Integration Ambassador",
]

ROUND_MARKERS: List[str] = [
    "Round 1",
    "Round 2",
    "Round 3",
    "Convergence",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "state": "roundtable_open",
            "updated_at": _now_iso(),
            "history": [],
            "discussion_validated": False,
            "discussion_log_path": "",
            "approval_queue": 0,
        }
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    state = str(data.get("state") or "roundtable_open")
    migrated = LEGACY_STATE_MAP.get(state, state)
    if migrated != state:
        data["state"] = migrated
    if "history" not in data or not isinstance(data["history"], list):
        data["history"] = []
    if "discussion_validated" not in data:
        data["discussion_validated"] = False
    if "discussion_log_path" not in data:
        data["discussion_log_path"] = ""
    if "approval_queue" not in data or not isinstance(data["approval_queue"], int):
        data["approval_queue"] = 0
    if migrated not in ORDER:
        data["state"] = "roundtable_open"
    return data


def save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def cmd_status(_: argparse.Namespace) -> int:
    data = load_state()
    print(json.dumps(data, indent=2))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    if args.state not in ORDER:
        print(f"Unknown state: {args.state}", file=sys.stderr)
        return 2

    data = load_state()
    current = data["state"]
    if args.state == current:
        print(f"Already in state: {current}")
        return 0

    allowed = ALLOWED_NEXT.get(current, [])
    if args.state not in allowed:
        print(
            f"Invalid transition: {current} -> {args.state}. Allowed: {allowed}",
            file=sys.stderr,
        )
        return 3
    if args.state == "discussion_completed" and not data.get("discussion_validated", False):
        print(
            "Invalid transition: discussion transcript is not validated. "
            "Run 'validate-discussion' first.",
            file=sys.stderr,
        )
        return 5

    entry = {
        "from": current,
        "to": args.state,
        "at": _now_iso(),
        "note": args.note or "",
    }
    data["history"].append(entry)
    data["state"] = args.state
    data["updated_at"] = entry["at"]
    if args.state == "roundtable_open":
        data["discussion_validated"] = False
        data["discussion_log_path"] = ""
    save_state(data)
    print(f"Transitioned: {current} -> {args.state}")
    return 0


def cmd_queue_approvals(args: argparse.Namespace) -> int:
    if args.count < 0:
        print("Approval count must be non-negative.", file=sys.stderr)
        return 10
    data = load_state()
    current = int(data.get("approval_queue") or 0)
    updated = current + int(args.count)
    now = _now_iso()
    data["approval_queue"] = updated
    data["updated_at"] = now
    data["history"].append(
        {
            "from": data["state"],
            "to": data["state"],
            "at": now,
            "note": args.note or f"approval_queue+={args.count} (now {updated})",
        }
    )
    save_state(data)
    print(f"Approval queue updated: {current} -> {updated}")
    return 0


def cmd_auto_approve(args: argparse.Namespace) -> int:
    data = load_state()
    current = data["state"]
    if current != "step_card_presented":
        print(
            f"Auto-approve blocked. Current state is '{current}', required 'step_card_presented'.",
            file=sys.stderr,
        )
        return 11
    queue = int(data.get("approval_queue") or 0)
    if queue <= 0:
        print("Auto-approve blocked. approval_queue is 0.", file=sys.stderr)
        return 12

    now = _now_iso()
    data["state"] = "human_approved"
    data["approval_queue"] = queue - 1
    data["updated_at"] = now
    data["history"].append(
        {
            "from": "step_card_presented",
            "to": "human_approved",
            "at": now,
            "note": args.note or "queued approval consumed",
        }
    )
    save_state(data)
    print(f"Auto-approved via queue. Remaining approvals: {data['approval_queue']}")
    return 0


def _validate_discussion_text(text: str) -> tuple[list[str], int]:
    missing = [persona for persona in REQUIRED_PERSONAS if persona not in text]
    rounds_seen = 0
    for marker in ROUND_MARKERS:
        if marker in text:
            rounds_seen += 1
    return missing, rounds_seen


def cmd_validate_discussion(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"Discussion log file not found: {path}", file=sys.stderr)
        return 6
    text = path.read_text(encoding="utf-8")
    missing, rounds_seen = _validate_discussion_text(text)
    if missing:
        print("Discussion validation failed. Missing personas:", file=sys.stderr)
        for persona in missing:
            print(f"- {persona}", file=sys.stderr)
        return 7
    if rounds_seen < 2:
        print(
            "Discussion validation failed. Need at least two round markers "
            "(e.g., Round 1 and Round 2).",
            file=sys.stderr,
        )
        return 8

    data = load_state()
    now = _now_iso()
    data["discussion_validated"] = True
    data["discussion_log_path"] = str(path)
    data["updated_at"] = now
    data["history"].append(
        {
            "from": data["state"],
            "to": data["state"],
            "at": now,
            "note": f"discussion_validated:{path}",
        }
    )
    save_state(data)
    print(f"Discussion validated for all 9 personas: {path}")
    return 0


def cmd_can_implement(_: argparse.Namespace) -> int:
    data = load_state()
    state = data["state"]
    if state != "human_approved":
        print(
            (
                "Implementation blocked. Current state is "
                f"'{state}', required state is 'human_approved'."
            ),
            file=sys.stderr,
        )
        return 4
    if not data.get("discussion_validated", False):
        print(
            "Implementation blocked. Discussion has not been validated.",
            file=sys.stderr,
        )
        return 9
    print("Implementation allowed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Human-in-the-loop process guard for open_llm_auth."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show current process state")
    status.set_defaults(func=cmd_status)

    set_cmd = sub.add_parser("set", help="Set next process state")
    set_cmd.add_argument("state", choices=ORDER)
    set_cmd.add_argument("--note", default="", help="Short note for this transition")
    set_cmd.set_defaults(func=cmd_set)

    queue_cmd = sub.add_parser(
        "queue-approvals",
        help="Add N queued human approvals for buffered execution mode",
    )
    queue_cmd.add_argument("count", type=int, help="Number of approvals to add")
    queue_cmd.add_argument("--note", default="", help="Short note for history")
    queue_cmd.set_defaults(func=cmd_queue_approvals)

    auto_approve = sub.add_parser(
        "auto-approve",
        help="Consume one queued approval and transition step_card_presented -> human_approved",
    )
    auto_approve.add_argument("--note", default="", help="Short note for history")
    auto_approve.set_defaults(func=cmd_auto_approve)

    validate_discussion = sub.add_parser(
        "validate-discussion",
        help="Validate discussion transcript contains all 9 personas and round markers",
    )
    validate_discussion.add_argument("path", help="Path to raw discussion transcript file")
    validate_discussion.set_defaults(func=cmd_validate_discussion)

    can_impl = sub.add_parser(
        "can-implement",
        help="Exit non-zero unless current state is human_approved",
    )
    can_impl.set_defaults(func=cmd_can_implement)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
