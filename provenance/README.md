
# 来源与哈希

server51.json、master.json、organizer50.json记录每个归档文件的源主机/路径、源SHA-256、当前归档SHA-256和脱敏类型。源文件未被修改。

INVENTORY.json列出题库、Case Manifest与文件总量。SOURCE_PACKAGES.json记录只读快照包哈希。verify工具检查的是归档内容，不能据此声称已验证未入Git的原始Parquet。

规范化时将源qa-gt-preview放入ground-truth/qa-preview，未把它误标为仅练习题。新的索引、说明和工具为本次整理生成，并非历史冻结文件。
