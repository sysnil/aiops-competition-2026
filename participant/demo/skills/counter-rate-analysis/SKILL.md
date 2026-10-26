---
name: counter-rate-analysis
description: Convert monotonic Counter metric samples into time-normalized rates before anomaly detection; use when Public API metrics include metric_type=counter.
handler: detectors.metrics:detect_counter_rates
version: 1.0.0
triggers:
  metric_type: counter
---

# Counter Rate Analysis

Use this skill only for series whose Public API record explicitly says `metric_type=counter`.

1. Group records by metric name, service, pod, node, and canonical labels.
2. Sort each series by its UTC timestamp.
3. Compute `rate = (current - previous) / elapsed_seconds` only for positive time deltas.
4. Treat negative deltas as counter resets and exclude those intervals. Do not fabricate a wrap value.
5. Exclude NULL, non-finite, duplicate-time, and overly sparse intervals.
6. Compare rates with a robust median/MAD baseline and require both a robust score and a rate ratio threshold.
7. Cite both source records for every derived rate so the conclusion is reproducible.

Never classify the absolute accumulated Counter value as a Gauge anomaly.

## Tools

Use the registered `query_metrics` tool with server-side time/entity filters. The
handler is `detectors.metrics:detect_counter_rates`; it is imported only after
the runtime selects this skill.

## Common false positives

- A large absolute total with a normal rate.
- A negative delta caused by process restart or reset.
- A high rate derived across a missing or abnormally long sampling interval.
- Comparing series with different labels as if they were one series.
- Declaring an isolated rate point without enough baseline samples.

## Evidence format

Every rate finding must cite the two Public API metric records used for the
delta, their timestamps, metric identity and entity labels. Skill discovery,
selection and execution statistics are written to `skill_usage.json`.

