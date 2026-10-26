#!/usr/bin/env python3
"""Read-only review gate for a proposed final scoring shortlist."""

import argparse
import json
from datetime import datetime
from pathlib import Path


MAX_EVENTS = 5
MIN_DURATION_SECONDS = 90


def assess(draft, shortlist):
    candidates = {event['event_id']: event for event in draft['merged_events']}
    selected = shortlist.get('scoring_event_ids', [])
    exclusions = shortlist.get('excluded_events', [])
    excluded = {item.get('event_id') for item in exclusions}
    issues = []
    if len(selected) != len(set(selected)):
        issues.append('DUPLICATE_EVENT_ID')
    if not 1 <= len(selected) <= MAX_EVENTS:
        issues.append('EVENT_COUNT_OUT_OF_RANGE')
    if (set(selected) & excluded or
            set(candidates) != set(selected) | excluded or
            len(excluded) != len(exclusions)):
        issues.append('EVENT_DISPOSITION_INCOMPLETE')
    for item in exclusions:
        if not item.get('reason') or item.get('review_status') != 'HUMAN_REJECTED':
            issues.append(f'EXCLUSION_NOT_REVIEWED:{item.get("event_id")}')
    for event_id in selected:
        event = candidates.get(event_id)
        if event is None:
            issues.append(f'UNKNOWN_EVENT:{event_id}')
            continue
        duration = (datetime.fromisoformat(event['end_time']) -
                    datetime.fromisoformat(event['start_time'])).total_seconds()
        if duration < MIN_DURATION_SECONDS:
            issues.append(f'SHORT_OBSERVED_EVENT:{event_id}')
        if not event.get('candidate_ids') or not event.get('evidence'):
            issues.append(f'PUBLIC_EVIDENCE_MISSING:{event_id}')
    if shortlist.get('review_status') != 'DRAFT_PENDING_HUMAN_REVIEW':
        issues.append('HUMAN_REVIEW_STATUS_INVALID')
    return {
        'status': 'GT_SHORTLIST_READY_FOR_HUMAN_REVIEW' if not issues
                  else 'GT_SHORTLIST_REVIEW_BLOCKED',
        'candidate_count': draft['candidate_count'],
        'merged_event_count': len(candidates),
        'proposed_scoring_event_count': len(selected),
        'issues': issues,
        'note': 'Every omitted event needs a recorded human rejection; source data is unchanged.'
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--shortlist', type=Path, required=True)
    args = parser.parse_args()
    result = assess(json.loads(args.draft.read_text()),
                    json.loads(args.shortlist.read_text()))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(result['status'] != 'GT_SHORTLIST_READY_FOR_HUMAN_REVIEW')


if __name__ == '__main__':
    main()
