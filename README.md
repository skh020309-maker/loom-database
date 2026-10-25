# Loom 笔记与想法数据库

为 Loom「想法收件箱 + AI 加工厂」准备可追溯的公开样本，支持摘要、标签、人工复核和检索实验。本期工作为 **2026-10-20 至 2026-10-25**，现已完成研究样本库最终交付。公开仓库：<https://github.com/skh020309-maker/loom-database>。

## 先看哪里

- `exports/浏览数据库.html`：双击即可搜索、筛选、查看原文短摘录、中文整理和来源。
- `loom.db`：已经生成的 SQLite 样本数据库。
- `exports/notes.csv`：Excel 可直接打开的 UTF-8 BOM 表格。
- `exports/notes.json`：供开发同学导入或调用。
- `exports/source_audit.json`：逐条网页来源再核查的证据与未解决项。
- `exports/data_quality.json`：重复、分组泄漏、缺失字段与覆盖情况。
- `exports/freeze_manifest_2026-10-24.json`：10月24日研究候选版的版本、数据指纹、文件清单及验收边界。
- `docs/最终交付_2026-10-25.md`：最终统计、覆盖缺口、测试日期、使用方法和交付边界。
- `exports/final_manifest_2026-10-25.json`：最终包文件清单与内容指纹；最终包用自己的清单核对。
- `docs/高校参考.md`：MIT、CMU、Stanford 的相关研究及可以采用的设计。
- `docs/每日推进计划.md`：本周每天的具体产出和周日验收标准。
- `docs/进度日志.md`：实际完成情况；计划数量不等于已采集数量。

## 当前范围

截至 **2026-10-25**：50 条公开笔记短摘录，23 位作者，中文 22 / 英文 28；开发集 37 / 测试集 13，分类为想法20、笔记17、问题9、既往事件4。今日新增/删除/修改笔记均0，正式库与10月24日候选版内容一致。最终版本`final-20261025-v1`是本期研究数据交付，全部50条标注仍AI辅助、待人工复核，真人金标准0，不代表产品验收或模型训练授权。

来源核查记录共50条，来自10月21日全量核查及10月22日有限重试，10月24日至25日未重访网页：此前49个原页可读，47条逐字匹配、2条仅行内代码规范化后匹配，作者归属49条确认；pub-021尚未成功核查。14条查到明确许可，35条尚未确认，1条未核查。来源表保留采集时的12条明确/38条未知，后来发现的两条CC BY-NC-SA 3.0另记在`source_checks`，没有静默改写原始批次。

本库收录**他人公开发表的笔记、想法的短摘录**及元数据。`original_excerpt` 是来源原文的片段，`summary_zh` 是 AI 辅助中文整理，不是作者原话，也不是人工标准答案。所有摘要和分类默认待人工复核。

全部50条摘要已做短摘录范围AI检查与交叉复查：中文21条、英文27条收窄到`original_excerpt`所支持的信息，2条原样保留；48条修订均在10月22日应用，旧摘要与理由保存在修订文件和数据库日志。10月23日另审查50个分类和150个标签：77个标签需上下文、8个有歧义；审查意见与建议单独保存，未自动改写现有分类或标签。全部50条仍为AI辅助、待人工复核，不是金标准，详见[候选检索与标注审查](docs/候选检索与标注审查_2026-10-23.md)。

原始笔记正文不会被摘要覆盖。来源中没有的日期、事件或作者意图不自动补成事实。没有运行 GLM 加工的条目保持 `pipeline_status=not_run`；导入成功只说明数据写入成功。

这是公开样本库的第一版。真实学生的随手速记、经授权的语音转写、手写 OCR 和同一用户连续多天的记录尚未覆盖。它不能用来证明产品已有真实用户、已通过效果评测，或具备完整语义检索。

## 与项目需求的关系

以本地 2026 年 10 月的《Loom需求分析文档》和《讲稿》为当前依据：UniApp 手机 App，Spring Boot 3 后端，MySQL 8，Redis，本地 SQLite。较早的 V2.6 文档属于网页方案，涉及技术路线或功能优先级冲突时，不据它覆盖较新的手机方案。

当前先交付便于组员复制、检查的 SQLite 样本库；`schema/mysql.sql` 提供 MySQL **8.0.29+** 的同构样本表和迁移参考，`exports/mysql_seed.sql` 提供数据导入脚本，只能导入独立的空开发库。**未在 MySQL 服务上执行，也未连接 App、Redis 或 GLM。**生产用户、认证和离线同步需结合正式代码另做迁移。

## 直接使用交付文件

解压`Loom数据库_2026-10-25_最终交付版.zip`后，进入`loom-database`文件夹：

- 双击`exports/浏览数据库.html`，即可离线搜索、筛选和查看来源，不需要Python、模型密钥或服务器。
- 用Excel打开`exports/notes.csv`，或由程序读取`exports/notes.json`；CSV保留公式前缀防护，完整原文以JSON为准。
- `loom.db`已经包含正式50条数据，可用SQLite工具只读查看。日常浏览无需执行init/import。

如果需要命令行检查，使用Python 3.10或以上，只依赖标准库。在本文件所在目录执行以下只读命令：

```powershell
python scripts/manage.py check
python scripts/manage.py stats
python scripts/manage.py search "笔记"
```

不要在随包正式库上重导来“准备使用”：笔记会跳过，但每次导入尝试仍会新增审计批次。最终包自己的`SHA256SUMS.txt`与`exports/final_manifest_2026-10-25.json`用于核对最终文件；旧冻结清单只对10月24日候选包有效。最终包`reference/`另附原候选zip，需另解压到独立目录，不与最终目录覆盖合并。

## 在独立空路径复建

以下用于开发者核验，创建另一份库，不改正式`loom.db`。若目标已经存在，先另取新名字：

```powershell
$loomRebuildDb = 'backups/rebuild-check.db'
if (Test-Path -LiteralPath $loomRebuildDb) { throw '目标已存在，请换一个新的复建文件名。' }
python scripts/manage.py --db $loomRebuildDb init
python scripts/manage.py --db $loomRebuildDb import data/seed_public_notes.json
python scripts/manage.py --db $loomRebuildDb import data/2026-10-21_public_notes.json
python scripts/manage.py --db $loomRebuildDb import data/2026-10-21_extra_zh_notes.json
python scripts/manage.py --db $loomRebuildDb import data/2026-10-21_extra_mixed_notes.json
python scripts/source_audits.py --db $loomRebuildDb import data/source_audit_2026-10-21_zh.json
python scripts/source_audits.py --db $loomRebuildDb import data/source_audit_2026-10-21_en.json
python scripts/apply_annotation_revision.py --db $loomRebuildDb data/annotation_revision_2026-10-21_zh.json
python scripts/apply_annotation_revision.py --db $loomRebuildDb data/annotation_revision_2026-10-22_en.json
python scripts/manage.py --db $loomRebuildDb check
python scripts/manage.py --db $loomRebuildDb stats
python scripts/manage.py --db $loomRebuildDb export --format json --output backups/rebuild-check.json
```

`search` 和 HTML 页面目前是关键词检索，不是向量检索或大模型语义搜索。来源再核查使用独立的`source_checks`表和追加记录，不改变摘要的人工复核状态或训练授权。重复导入同一文件不会新增重复记录；原文变更需新版本/新记录，不能借重复导入覆写。

本期每日任务已在10月25日停止，自动调度项`loom-10-25`已删除，资料与版本记录保留，后续不自动新增采集或修改数据库。若以后另开建设阶段，新增数据仍应独立版本化、保持既有ID和人工保护字段，再按真实授权与来源执行导入、检查和导出。

MySQL 导出保留全部 8 张表，不覆盖旧数据；URL 最长 768 字符，笔记 ID 和标签最长 191 字符，超限会报错。需要的 MySQL 版本高于一般的“8.0”，是因为建表脚本使用可重复执行的触发器定义。

## 检索验收现状

已建立16题候选基线（14正例、2负例），目标与标签均待人工复核。当前50条快照中，中文自然表达与模糊回忆0/10命中，英文原词4/4命中；当前实现是SQL LIKE字面匹配。10月22日摘要修订后，固定预指定关键词对照由14/14降为8/14；没有修改题目或关键词追求较高分数。这一对照仅检查可检索字段，不代表自动关键词提取或语义理解。完整指标与语料指纹见 `exports/retrieval_eval.json`。

10月23日对16题与全部50条记录审查后，发现广义SQLite查询`ret-011`遗漏`pub-011`、`pub-020`两个相关文档候选；两道原负例在当前语料中仍无直接支持。另有12个正例的摘录不足以确认题目全部限定或命名，标题可以合法定位文档，但不等于短摘录足够回答问题。审查没有改固定题集、分组或分数，也没有把正例强改为负例。当日实际SQL LIKE结果仍为整句4/14、预指定关键词8/14；快照见`exports/retrieval_eval_2026-10-23.json`，双口径意见见`exports/retrieval_label_audit.json`。

组员可用 `scripts/review_notes.py` 修订和确认候选标注，修改会保留前后差异；浏览页支持按复核状态筛选。当前50条都还没有被标成人工已核验。

## 10月24日冻结候选版

默认字符3-gram检查没有近重复候选，作者与源文档跨组均为0；默认只比较32条/496对，另18条短摘录不参加该评分。补充全长度诊断与AI范围检查后，保留8组相邻主题或字面相似的说明，不自动合并，也不据此证明不存在语义重复。JSON、CSV的26列、Markdown记录块、HTML嵌入数据及MySQL种子逐字段核对通过。

从4个种子、2个来源核查和2个摘要修订文件独立复建，50条业务记录及8表业务字段一致；实际创建/更新时间不同，未伪装成原执行日期。重放跳过50条笔记、50条来源核查及48条修订，仅临时库新增4个导入尝试审计批次。正式库文件哈希未变。代码未改，69项单元测试仍以10月22日的实际通过记录为准，今日未重复运行。

候选包为`dist/Loom数据库_2026-10-24_冻结候选版.zip`，包内`SHA256SUMS.txt`和冻结清单可检查每个文件。10月25日先对照该包与稳定业务指纹，再作最终交付；不得通过改题、自动套用pending建议或回填真人核验状态来消除限制。

## 10月25日最终验收

候选包原SHA256、69项包内校验与68项冻结清单全部匹配，40个保护文件及50条完整业务记录与候选版一致，独立比较18项通过。今日刷新并逐字段检查JSON/CSV/Markdown/HTML、来源核查、MySQL种子和SQL LIKE基线；8表591行与正式库一致，数据库文件哈希未变。69项单元测试通过日期仍为10月22日，代码未变，今日未重跑。

最终包为`dist/Loom数据库_2026-10-25_最终交付版.zip`。完整统计、测试日期、来源和许可缺口、标注范围与使用方法见[最终交付报告](docs/最终交付_2026-10-25.md)，机器可读结果见`exports/final_statistics_2026-10-25.json`和`exports/final_export_validation_2026-10-25.json`。最终验收只确认本期数据交付与证据一致，没有把AI待审标签变成人工金标准，也没有完成本研究库与生产服务或手机App的联调。

GitHub仓库包含数据、导出和源文件；本聊天另外交付带原候选zip附档的完整归档包。`dist/`、备份及临时库不进入GitHub版本管理。

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
- [10月25日最终交付报告](docs/最终交付_2026-10-25.md)
