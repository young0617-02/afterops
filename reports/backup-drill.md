# 备份恢复演练

- 时间：2026-11-03 14:59:55
- 方式：`pg_dump` → 临时库 `afterops_drill_20261103_145949` 恢复 → 计数校验 → 删除临时库；uploads 归档 → 临时目录 SHA-256 校验
- 数据库备份：`D:\project\ecommerce-support-agent\backups\afterops-20261103-145949.sql.gz`（368331 bytes，gzip）
- 附件归档：`D:\project\ecommerce-support-agent\backups\afterops-attachments-20261103-145949.tar.gz`（335 bytes，tar.gz）
- 结果：**PASS**
- 恢复后表数：30；merchants：1；orders：361
- 附件：DB 3 个，归档 SHA-256 匹配 3 个
- 临时库清理：已删除

> 演练不修改主库 `afterops`；数据库恢复到临时库，附件只解压到临时目录做哈希校验，不覆盖线上卷。
> 本演练只验证备份文件可恢复、附件归档可解压且哈希一致，不覆盖 PITR、跨机恢复或恢复时长目标。
