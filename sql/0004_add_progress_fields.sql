-- 迁移脚本：为 report_task 表新增进度、状态描述和错误日志字段
-- 对应 Django 迁移：0004_reporttask_progress_message_error_message
-- task_status 已由代码层面扩展至 4（取消中），TINYINT/SMALLINT 兼容，无需 DDL 变更

ALTER TABLE report_task
  ADD COLUMN `progress`      TINYINT       NOT NULL DEFAULT 0   COMMENT '任务处理进度：1-100',
  ADD COLUMN `message`       VARCHAR(1000) DEFAULT NULL         COMMENT '状态描述（已完成图片追加记录）',
  ADD COLUMN `error_message` VARCHAR(1000) DEFAULT NULL         COMMENT '错误日志';
