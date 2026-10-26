# Formal Final rubric revision

Status: DEPLOYED, 2026-10-20. Applies to future official Final submissions from all teams. Final tasks remain closed; no historical grade was changed.

The competition server at `192.168.51.51` performs grading. The participant Web on 52 and its clones displays that server's result, so the rule was installed centrally rather than separately on each team machine.

The versioned policy is `final-balanced-evidence-v2.0`: type/entity/time/evidence weights stay 40/20/20/20; timing tolerance changes from 120 to 300 seconds per boundary; false-positive and duplicate deductions change from 0.5 to 0.2 points per event on the final 10-point scale. Positive-credit matching, verified Public evidence, and the 0-10 clamp remain. Empty answers earn 0; there is no artificial score floor.

Read-only shadow scoring on the seven saved baseline answers, using the same approved GT and verified Public evidence:

| Task | Previous /10 | New /10 |
| --- | ---: | ---: |
| FT-01 | 0.5 | 0.75 |
| FT-02 | 1.5 | 2.9 |
| FT-03 | 0 | 3.0 |
| PK-R01 | 3.0 | 3.5 |
| PK-R02 | 0 | 0 |
| PK-R03 | 2.167 | 3.067 |
| PK-R04 | 2.5 | 5.0 |

These are offline sensitivity results, not contestant submissions or retroactive grades. The unchanged zero for PK-R02 reflects its empty answer. The rubric reduces timing and false-positive penalties but does not repair wrong entity selection or missing evidence in the baseline Agent.

The policy file SHA-256 is `606ea0cfd304fdf6c71d1116632b005d623563f80198af86796bccdfebb77466`; the approved GT Manifest pin remains `918c90331f260075987ca16164088c76fb2d716d73fe0ee5e631c2dba65d331c`. The formal grader checks both pins and fails closed if the rule is missing or changed. Internal preview and Semifinal code paths retain their previous rubrics.

Verification: five focused policy tests passed, the full competition API/grading test suite passed with three existing skips, the private grader was restarted and is active, and a direct live-grader smoke with an empty answer returned `COMPLETED`, score `0.0`, rubric `final-balanced-evidence-v2.0`, mode `OFFICIAL`. No formal Final submission was created. At verification, the official submission count was zero and all Final tasks were `NOT_OPEN`.
