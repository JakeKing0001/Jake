import json

from core.risk import RiskLevel
from core.voice.dialogue import (
    CorrectionPlanner,
    DialogueContext,
    ReplyKind,
    classify_reply,
)
from core.voice.language_normalizer import (
    resolve_ellipsis,
    resolve_ordinals,
)


def main() -> int:
    total = 0
    errors = 0

    reply_cases = [
        (
            "si",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.CONFIRM_YES,
        ),
        (
            "no",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.CONFIRM_NO,
        ),
        (
            "si ma prima apri spotify",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.NEW_COMMAND,
        ),
        (
            "che ore sono",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.NEW_COMMAND,
        ),
        (
            "Marco",
            DialogueContext(
                awaiting_clarification=True
            ),
            ReplyKind.CLARIFICATION_ANSWER,
        ),
        (
            "apri spotify",
            DialogueContext(
                awaiting_clarification=True
            ),
            ReplyKind.NEW_COMMAND,
        ),
        (
            "password-super-segreta",
            DialogueContext(
                awaiting_auth=True
            ),
            ReplyKind.AUTH_SECRET,
        ),
        (
            "yes",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.CONFIRM_YES,
        ),
        (
            "cancel",
            DialogueContext(
                pending_confirmation=True
            ),
            ReplyKind.CONFIRM_NO,
        ),
        (
            "Jake apri spotify",
            DialogueContext(),
            ReplyKind.NEW_COMMAND,
        ),
    ]

    for text, context, expected in reply_cases:
        total += 1

        actual = classify_reply(
            text,
            context,
        ).kind

        if actual != expected:
            errors += 1

    ellipsis_cases = [
        (
            "e a Milano?",
            "GET_WEATHER",
            {"city": "Roma"},
            True,
        ),
        (
            "e a Torino?",
            "GET_WEATHER",
            {"city": "Roma"},
            True,
        ),
        (
            "e apri Spotify",
            "GET_WEATHER",
            {"city": "Roma"},
            False,
        ),
        (
            "e Marco",
            "SEND_MESSAGE",
            {
                "contact": "Luca",
                "text": "ciao",
            },
            False,
        ),
    ]

    for text, intent, params, expected_ok in (
        ellipsis_cases
    ):
        total += 1

        result = resolve_ellipsis(
            text,
            intent,
            params,
        )

        if (result is not None) != expected_ok:
            errors += 1

    ordinal_cases = [
        (
            "apri il primo",
            5,
            [0],
        ),
        (
            "apri il primo e il terzo",
            5,
            [0, 2],
        ),
        (
            "apri l'ultimo",
            5,
            [4],
        ),
        (
            "apri i primi due",
            5,
            [0, 1],
        ),
        (
            "apri il decimo",
            3,
            None,
        ),
    ]

    for text, count, expected in ordinal_cases:
        total += 1

        actual = resolve_ordinals(
            text,
            count,
        )

        if actual != expected:
            errors += 1

    planner = CorrectionPlanner()

    planner.record(
        "read",
        "che ore sono",
        "GET_TIME",
        {},
        RiskLevel.READ_ONLY,
        "executed",
    )

    planner.record(
        "local",
        "apri spotifi",
        "OPEN_APP",
        {"app": "spotifi"},
        RiskLevel.LOCAL_REVERSIBLE,
        "executed",
        reversible=True,
    )

    planner.record(
        "external",
        "manda ciao",
        "SEND_EMAIL",
        {},
        RiskLevel.EXTERNAL_ACTION,
        "executed",
    )

    planner_cases = [
        ("read", "rerun"),
        ("local", "undo_then_rerun"),
        ("external", "ask"),
    ]

    unsafe_reruns = 0

    for action_id, expected in planner_cases:
        total += 1

        plan = planner.plan(action_id)

        if plan.action != expected:
            errors += 1

        if (
            action_id == "external"
            and plan.action == "rerun"
        ):
            unsafe_reruns += 1

    correction_rate = (
        errors / total
        if total
        else 1.0
    )

    report = {
        "total_turns": total,
        "first_try_correct": total - errors,
        "correction_required": errors,
        "correction_rate": correction_rate,
        "correction_rate_percent": (
            correction_rate * 100
        ),
        "unsafe_reruns": unsafe_reruns,
        "pass": (
            correction_rate < 0.05
            and unsafe_reruns == 0
        ),
    }

    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
    )

    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())