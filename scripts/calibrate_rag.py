#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_EVAL_FILE = Path("tests/golden/phase4_qa_eval.json")


@dataclass(frozen=True)
class Candidate:
    min_score: float
    min_evidence_score: float
    accuracy: float
    answer_precision: float
    answer_recall: float
    answer_f1: float
    refusal_recall: float
    answered: int
    refused: int
    false_answers: int
    false_refusals: int


def _frange(start: float, stop: float, step: float) -> list[float]:
    values: list[float] = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 3))
        current += step
    return values


def _scores_for_case(case: dict[str, Any]) -> list[float]:
    raw_scores = case.get("retrieval_scores")
    if isinstance(raw_scores, list):
        return [float(score) for score in raw_scores]
    if "top_score" in case:
        return [float(case["top_score"])]

    # Backward-compatible fallback for the tiny existing golden pack.
    # Real calibration should add retrieval_scores captured from real eval runs.
    return [0.9] if not case["expected_insufficient_context"] else [0.0]


def _predict_answerable(
    scores: list[float],
    *,
    min_score: float,
    min_evidence_score: float,
) -> bool:
    filtered = [score for score in scores if score >= min_score]
    return bool(filtered) and max(filtered) >= min_evidence_score


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate(
    cases: list[dict[str, Any]],
    *,
    min_score: float,
    min_evidence_score: float,
) -> Candidate:
    true_answers = false_answers = true_refusals = false_refusals = 0

    for case in cases:
        expected_answerable = not bool(case["expected_insufficient_context"])
        predicted_answerable = _predict_answerable(
            _scores_for_case(case),
            min_score=min_score,
            min_evidence_score=min_evidence_score,
        )
        if predicted_answerable and expected_answerable:
            true_answers += 1
        elif predicted_answerable and not expected_answerable:
            false_answers += 1
        elif not predicted_answerable and expected_answerable:
            false_refusals += 1
        else:
            true_refusals += 1

    total = max(1, len(cases))
    answer_precision = true_answers / max(1, true_answers + false_answers)
    answer_recall = true_answers / max(1, true_answers + false_refusals)
    refusal_recall = true_refusals / max(1, true_refusals + false_answers)
    return Candidate(
        min_score=min_score,
        min_evidence_score=min_evidence_score,
        accuracy=(true_answers + true_refusals) / total,
        answer_precision=answer_precision,
        answer_recall=answer_recall,
        answer_f1=_f1(answer_precision, answer_recall),
        refusal_recall=refusal_recall,
        answered=true_answers + false_answers,
        refused=true_refusals + false_refusals,
        false_answers=false_answers,
        false_refusals=false_refusals,
    )


def choose_best(candidates: list[Candidate]) -> Candidate:
    return max(
        candidates,
        key=lambda item: (
            item.answer_f1,
            item.refusal_recall,
            item.accuracy,
            item.min_evidence_score,
            item.min_score,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sweep strict-RAG thresholds over a golden Q&A eval file."
    )
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--step", type=float, default=0.05)
    args = parser.parse_args()

    cases = json.loads(args.eval_file.read_text())
    min_scores = _frange(0.0, 0.8, args.step)
    evidence_scores = _frange(0.0, 0.9, args.step)
    candidates = [
        evaluate(
            cases,
            min_score=min_score,
            min_evidence_score=min_evidence_score,
        )
        for min_score in min_scores
        for min_evidence_score in evidence_scores
    ]
    best = choose_best(candidates)
    payload = {
        "eval_file": str(args.eval_file),
        "case_count": len(cases),
        "recommended_project_settings": {
            "qa": {
                "min_score": best.min_score,
                "min_evidence_score": best.min_evidence_score,
            }
        },
        "best": asdict(best),
        "top_candidates": [
            asdict(candidate)
            for candidate in sorted(
                candidates,
                key=lambda item: (
                    item.answer_f1,
                    item.refusal_recall,
                    item.accuracy,
                ),
                reverse=True,
            )[:10]
        ],
    }

    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.write_text(f"{text}\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
