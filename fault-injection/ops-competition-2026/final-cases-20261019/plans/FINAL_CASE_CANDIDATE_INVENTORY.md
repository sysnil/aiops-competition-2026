# 决赛候选 Case 清单（规划 12，实际可用 7）

| Case | 来源 | 实际状态 | 机制 | 用途分配 |
|---|---|---|---|---|
| SF-004 | 既有 | READY_FOR_IMPORT | HTTP ReqDelay | 未分配 |
| SF-006 | 既有 | READY_FOR_IMPORT | PodMemory60 | 未分配 |
| SF-007 | 既有 | READY_FOR_IMPORT | ServicePause30 | 未分配 |
| SF-008 | 既有 | READY_FOR_IMPORT | PodMemory300 | 未分配 |
| SF-009 | 既有 | READY_FOR_IMPORT | HTTP ReqDelay + ServicePause | 未分配 |
| SF-011 | 既有 | READY_FOR_IMPORT | HTTP ReqDelay | 未分配 |
| SF-012 | 既有 | READY_FOR_IMPORT | PodMemory300 | 未分配 |
| SF-013 | 新增 | BLOCKED_BY_SAFETY，ATTEMPT-01/02 均故障前失败 | PodCPUStress 未资格；PodMemory fallback 未产生正式数据 | 未分配 |
| SF-014 | 新增 | NOT_STARTED | ServicePause30 草案 | 未分配 |
| SF-015 | 新增 | NOT_STARTED | HTTP ReqDelay 草案 | 未分配 |
| SF-016 | 新增 | NOT_STARTED | PodMemory300 草案 | 未分配 |
| SF-017 | 新增 | NOT_STARTED | HTTP ReqDelay + ServicePause 草案 | 未分配 |

既有七题的 READY 以 `/root/ops-competition-2026/plans/SF-003-to-SF-012-master-ready-handoff-20261018.json` 为准。新增五题交接索引中的 READY entries 为空；未向组委会指定上半场题、PK 轮次或备用题，也未导入或发布到比赛服务器。全部 GT 草案仍需人工审核。
