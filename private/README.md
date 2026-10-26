
# 组委会私有评测资产

此目录只适用于Private仓库，不得由Public API、Nginx静态目录或选手Web直接提供。

- `ground-truth/semifinal/`：从51现行`qa-gt`归档的Q01–Q10正式QA标准答案。
- `ground-truth/final/`：从51现行`final-gt-v2-20261020`归档的12个决赛Case正式GT及Manifest。
- `ground-truth/qa-preview/`：源`qa-gt-preview`的历史预览资料，可能含正式题旧版本，不能作为现行正式GT替代品。
- `scoring/`：51现行正式决赛评分配置。

原始生成报告及待审核GT在`questions/semifinal/generation/private/`保留，以说明来源；部署时优先使用本目录的现行正式资产。任何审核状态应以源文件字段和实际流程为准，本次归档没有批准新GT。
