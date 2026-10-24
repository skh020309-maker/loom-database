# Loom 笔记与想法数据库

为 Loom「想法收件箱 + AI 加工厂」准备可追溯的公开样本，支持摘要、标签、人工复核和检索实验。首日为 **2026-10-20**；每日继续到 **2026-10-25（周日）**。公开仓库：<https://github.com/skh020309-maker/loom-database>。

## 先看哪里

- `exports/浏览数据库.html`：双击即可搜索、筛选、查看原文短摘录、中文整理和来源。
- `loom.db`：已经生成的 SQLite 样本数据库。
- `exports/notes.csv`：Excel 可直接打开的 UTF-8 BOM 表格。
- `exports/notes.json`：供开发同学导入或调用。
- `exports/source_audit.json`：逐条网页来源再核查的证据与未解决项。
- `exports/data_quality.json`：重复、分组泄漏、缺失字段与覆盖情况。
- `exports/freeze_manifest_2026-10-24.json`：10月24日研究候选版的版本、数据指纹、文件清单及验收边界。
- `docs/高校参考.md`：MIT、CMU、Stanford 的相关研究及可以采用的设计。
- `docs/每日推进计划.md`：本周每天的具体产出和周日验收标准。
- `docs/进度日志.md`：实际完成情况；计划数量不等于已采集数量。

## 当前范围

截至 **2026-10-24**：50 条公开笔记短摘录，23 位作者，中文 22 / 英文 28；开发集 37 / 测试集 13，摘要及分类均待人工复核。今日新增笔记0，已完成近重复、作者/源文档分组、独立复建和导出一致性检查，冻结研究候选版`rc-20261024-v1`。原文及已有标注未改；[冻结说明](docs/冻结候选版_2026-10-24.md)列明未解决项，候选版不等于真人金标准或产品验收通过。

来源核查记录共50条，来自10月21日全量核查及10月22日有限重试，10月24日未重访网页：此前49个原页可读，47条逐字匹配、2条仅行内代码规范化后匹配，作者归属49条确认；pub-021尚未成功核查。14条查到明确许可，35条尚未确认，1条未核查。来源表保留采集时的12条明确/38条未知，后来发现的两条CC BY-NC-SA 3.0另记在`source_checks`，没有静默改写原始批次。

本库收录**他人公开发表的笔记、想法的短摘录**及元数据。`original_excerpt` 是来源原文的片段，`summary_zh` 是 AI 辅助中文整理，不是作者原话，也不是人工标准答案。所有摘要和分类默认待人工复核。

全部50条摘要已做短摘录范围AI检查与交叉复查：中文21条、英文27条收窄到`original_excerpt`所支持的信息，2条原样保留；48条修订均在10月22日应用，旧摘要与理由保存在修订文件和数据库日志。10月23日另审查50个分类和150个标签：77个标签需上下文、8个有歧义；审查意见与建议单独保存，未自动改写现有分类或标签。全部50条仍为AI辅助、待人工复核，不是金标准，详见[候选检索与标注审查](docs/候选检索与标注审查_2026-10-23.md)。

原始笔记正文不会被摘要覆盖。来源中没有的日期、事件或作者意图不自动补成事实。没有运行 GLM 加工的条目保持 `pipeline_status=not_run`；导入成功只说明数据写入成功。

这是公开样本库的第一版。真实学生的随手速记、经授权的语音转写、手写 OCR 和同一用户连续多天的记录尚未覆盖。它不能用来证明产品已有真实用户、已通过效果评测，或具备完整语义检索。

## 与项目需求的关系

以本地 2026 年 10 月的《Loom需求分析文档》和《讲稿》为当前依据：UniApp 手机 App，Spring Boot 3 后端，MySQL 8，Redis，本地 SQLite。较早的 V2.6 文档属于网页方案，涉及技术路线或功能优先级冲突时，不据它覆盖较新的手机方案。

当前先交付便于组员复制、检查的 SQLite 样本库；`schema/mysql.sql` 提供 MySQL **8.0.29+** 的同构样本表和迁移参考，`exports/mysql_seed.sql` 提供数据导入脚本，只能导入独立的空开发库。**未在 MySQL 服务上执行，也未连接 App、Redis 或 GLM。**生产用户、认证和离线同步需结合正式代码另做迁移。

## 使用

需要 Python 3.10 或以上；只有 Python 标准库，不需要安装依赖。在本文件所在目录执行：

```powershell
python scripts/manage.py init
python scripts/manage.py import data/seed_public_notes.json
python scripts/manage.py import data/2026-10-21_public_notes.json
python scripts/manage.py import data/2026-10-21_extra_zh_notes.json
python scripts/manage.py import data/2026-10-21_extra_mixed_notes.json
python scripts/source_audits.py import data/source_audit_2026-10-21_zh.json
python scripts/source_audits.py import data/source_audit_2026-10-21_en.json
python scripts/apply_annotation_revision.py data/annotation_revision_2026-10-21_zh.json
python scripts/apply_annotation_revision.py data/annotation_revision_2026-10-22_en.json
python scripts/manage.py check
python scripts/manage.py stats
python scripts/manage.py search "笔记"
python scripts/manage.py export --format json
python scripts/manage.py export --format csv
python scripts/manage.py export --format markdown
python scripts/source_audits.py export
python scripts/audit_quality.py
python scripts/build_viewer.py
python scripts/export_mysql.py
python scripts/review_notes.py show pub-001
python scripts/evaluate_retrieval.py
python -m unittest discover -s tests -v
```

`search` 和 HTML 页面目前是关键词检索，不是向量检索或大模型语义搜索。来源再核查使用独立的`source_checks`表和追加记录，不改变摘要的人工复核状态或训练授权。重复导入同一文件不会新增重复记录；原文变更需新版本/新记录，不能借重复导入覆写。

后续每日任务当前设为 **22:30（北京时间）**，周日验收后暂停。每日新增数据使用独立文件，例如 `data/2026-10-21_public_notes.json`，保持已有 ID 不变，再执行上述导入、检查和导出。

MySQL 导出保留全部 8 张表，不覆盖旧数据；URL 最长 768 字符，笔记 ID 和标签最长 191 字符，超限会报错。需要的 MySQL 版本高于一般的“8.0”，是因为建表脚本使用可重复执行的触发器定义。

## 检索验收现状

已建立16题候选基线（14正例、2负例），目标与标签均待人工复核。当前50条快照中，中文自然表达与模糊回忆0/10命中，英文原词4/4命中；当前实现是SQL LIKE字面匹配。10月22日摘要修订后，固定预指定关键词对照由14/14降为8/14；没有修改题目或关键词追求较高分数。这一对照仅检查可检索字段，不代表自动关键词提取或语义理解。完整指标与语料指纹见 `exports/retrieval_eval.json`。

10月23日对16题与全部50条记录审查后，发现广义SQLite查询`ret-011`遗漏`pub-011`、`pub-020`两个相关文档候选；两道原负例在当前语料中仍无直接支持。另有12个正例的摘录不足以确认题目全部限定或命名，标题可以合法定位文档，但不等于短摘录足够回答问题。审查没有改固定题集、分组或分数，也没有把正例强改为负例。当日实际SQL LIKE结果仍为整句4/14、预指定关键词8/14；快照见`exports/retrieval_eval_2026-10-23.json`，双口径意见见`exports/retrieval_label_audit.json`。

组员可用 `scripts/review_notes.py` 修订和确认候选标注，修改会保留前后差异；浏览页支持按复核状态筛选。当前50条都还没有被标成人工已核验。

## 10月24日冻结候选版

默认字符3-gram检查没有近重复候选，作者与源文档跨组均为0；默认只比较32条/496对，另18条短摘录不参加该评分。补充全长度诊断与AI范围检查后，保留8组相邻主题或字面相似的说明，不自动合并，也不据此证明不存在语义重复。JSON、CSV的26列、Markdown记录块、HTML嵌入数据及MySQL种子逐字段核对通过。

从4个种子、2个来源核查和2个摘要修订文件独立复建，50条业务记录及8表业务字段一致；实际创建/更新时间不同，未伪装成原执行日期。重放跳过50条笔记、50条来源核查及48条修订，仅临时库新增4个导入尝试审计批次。正式库文件哈希未变。代码未改，69项单元测试仍以10月22日的实际通过记录为准，今日未重复运行。

候选包为`dist/Loom数据库_2026-10-24_冻结候选版.zip`，包内`SHA256SUMS.txt`和冻结清单可检查每个文件。10月25日先对照该包与稳定业务指纹，再作最终交付；不得通过改题、自动套用pending建议或回填真人核验状态来消除限制。

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
- [人工复核操作](docs/人工复核操作.md)
- [检索评测说明](docs/检索评测说明.md)
- [10月21日交付记录](docs/今日交付_2026-10-21.md)

- [来源核查与未解决项](docs/来源核查.md)
- [数据质量检查方法](docs/数据质量检查.md)

- [中文摘要范围检查](docs/中文摘要检查_2026-10-21.md)

- [英文摘要范围检查](docs/英文摘要检查_2026-10-22.md)
- [10月22日交付记录](docs/今日交付_2026-10-22.md)
- [10月23日候选检索与标注审查](docs/候选检索与标注审查_2026-10-23.md)
- [10月23日交付记录](docs/今日交付_2026-10-23.md)
- [10月24日冻结候选版](docs/冻结候选版_2026-10-24.md)
- [10月24日交付记录](docs/今日交付_2026-10-24.md)
