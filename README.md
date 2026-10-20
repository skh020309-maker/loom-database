# Loom 笔记与想法数据库

为 Loom「想法收件箱 + AI 加工厂」准备可追溯的公开样本，支持摘要、标签、人工复核和检索实验。首日为 **2026-10-20**；每日继续到 **2026-10-25（周日）**。

## 先看哪里

- `exports/浏览数据库.html`：双击即可搜索、筛选、查看原文短摘录、中文整理和来源。
- `loom.db`：已经生成的 SQLite 样本数据库。
- `exports/notes.csv`：Excel 可直接打开的 UTF-8 BOM 表格。
- `exports/notes.json`：供开发同学导入或调用。
- `docs/高校参考.md`：MIT、CMU、Stanford 的相关研究及可以采用的设计。
- `docs/每日推进计划.md`：本周每天的具体产出和周日验收标准。
- `docs/进度日志.md`：实际完成情况；计划数量不等于已采集数量。

## 当前范围

本库收录**他人公开发表的笔记、想法的短摘录**及元数据。`original_excerpt` 是来源原文的片段，`summary_zh` 是 AI 辅助中文整理，不是作者原话，也不是人工标准答案。所有摘要和分类默认待人工复核。

原始笔记正文不会被摘要覆盖。来源中没有的日期、事件或作者意图不自动补成事实。没有运行 GLM 加工的条目保持 `pipeline_status=not_run`；导入成功只说明数据写入成功。

这是公开样本库的第一版。真实学生的随手速记、经授权的语音转写、手写 OCR 和同一用户连续多天的记录尚未覆盖。它不能用来证明产品已有真实用户、已通过效果评测，或具备完整语义检索。

## 与项目需求的关系

以本地 2026 年 10 月的《Loom需求分析文档》和《讲稿》为当前依据：UniApp 手机 App，Spring Boot 3 后端，MySQL 8，Redis，本地 SQLite。较早的 V2.6 文档属于网页方案，涉及技术路线或功能优先级冲突时，不据它覆盖较新的手机方案。

当前先交付便于组员复制、检查的 SQLite 样本库；`schema/mysql.sql` 提供 MySQL 8 的同构样本表和迁移参考，`exports/mysql_seed.sql` 提供数据导入脚本。**未在 MySQL 服务上执行，也未连接 App、Redis 或 GLM。**生产用户、认证和离线同步需结合正式代码另做迁移。

## 使用

需要 Python 3.10 或以上；只有 Python 标准库，不需要安装依赖。在本文件所在目录执行：

```powershell
python scripts/manage.py init
python scripts/manage.py import data/seed_public_notes.json
python scripts/manage.py check
python scripts/manage.py stats
python scripts/manage.py search "笔记"
python scripts/manage.py export --format json
python scripts/manage.py export --format csv
python scripts/manage.py export --format markdown
python scripts/build_viewer.py
python scripts/export_mysql.py
python -m unittest discover -s tests -v
```

`search` 和 HTML 页面目前是关键词检索，不是向量检索或大模型语义搜索。重复导入同一文件不会新增重复记录；原文变更需新版本/新记录，不能借重复导入覆写。

每日新增数据使用独立文件，例如 `data/2026-10-21_public_notes.json`，保持已有 ID 不变，再执行上述导入、检查和导出。

## 数据责任

- 每条保留作者、原始 URL、原文位置、采集日期、内容来源类型和许可状态。
- `license_status=open` 只表示查到明确许可；实际限制由 `license_name` 与 `license_url` 决定，不等于可以商用。
- 未查到授权时标 `unknown`，只保存短摘录与元数据。所有条目 `training_allowed=0`，未获授权不建立全文训练库。
- 整个仓库没有统一授予第三方内容的许可，引用权利仍属于各作者。禁止用代码许可证覆盖第三方笔记。
- 按作者分组设置开发集与测试集；同一作者不跨集合。候选标签尚不是人工金标准，不能直接据此宣称分类准确率。
- 只对新任务文件做版本管理，不上传本地原始 Word/PPT、同学私密笔记或凭证。

## 文档

- [数据库设计与接口边界](docs/数据库设计.md)
- [采集与标注规范](docs/采集与标注规范.md)
- [每日推进计划](docs/每日推进计划.md)
- [进度日志](docs/进度日志.md)
- [仓库状态](docs/仓库状态.md)
- [高校参考](docs/高校参考.md)
