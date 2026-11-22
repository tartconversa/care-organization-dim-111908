# 服务机构改组后的多归属维度装载

组织、归属边、承接交接与服务原始资料，以及在其上的维度接收、事实关联、
口径发布与多视角查询。所有人员、机构、地址、业务事件及数值均为合成，
不来自真实企业或个人。

本仓库交付四层能力：

1. **维度接收**（`careorg/source.py`、`careorg/validate.py`）
   法人、服务责任、财务汇总三套组织关系各自独立；逐视角检查上级边、
   有效段与站点分担份额，问题只隔离对应视角的对应时段。
2. **事实关联**（`careorg/facts.py`、`careorg/restate.py`）
   同一批来源服务事实可按原归属，或在“新组织口径”下按可证明的承接范围
   重新解释；份额、金额、次数分别遵守各自的可加性约定。
3. **口径发布**（`careorg/publish.py`）
   发布记录不可覆盖，固定观察日、组织关系、重述依据与维表指纹；
   资料撤回后当前口径失效，历史固定版本仍可解释当时依据。
4. **多视角查询**（`careorg/query.py`）
   明细、按机构/来源/站点/村庄汇总，以及按 `service_id` 对齐的跨视角对照。

## 资料约定

`docs/source-contract.md` 解释来源身份、适用范围和业务含义。
`data/catalog.json` 登记来源文件格式、条数、字段与内容摘要。
来源指纹按 **LF 规范化后的内容** 计算，避免 Git 换行设置影响校验。
`support/check_inputs.py` 只检查资料可读性与引用，不产生业务结果。

## 关键语义

- **三棵树互不代替**：`legal` 是法人控制，`responsibility` 是服务责任，
  `finance` 是财务归集。`legal` 视角始终取服务的来源法人；财务与服务责任
  分别读取各自的站点归属关系，不会因为服务责任改判而跟着改判。
- **有效区间左闭右开**：观察日落在区间内才生效；空结束值不推断未来。
- **份额**：同一站点/服务/视角下可按多条关系分担。合计不足 1 时，剩余
  份额保留为未归属；合计超过 1 视为争议，不归一、不平均，该时段整体隔离。
- **上级关系**：同一时段出现循环或重复上级时，仅隔离该视角的该时段，
  不替其他树作结论。
- **承接重述**：通知必须已生效、已收到，且同一通知在对应站点/服务上有
  覆盖观察日的实际交接，才算“可证明的承接”。只改名称不换主体的机构
  保留原 `org_id`；通知与交接分开到达时，未覆盖范围显示“尚未具备重述条件”
  （`restatement_not_ready`），不平均摊给新单位。
- **可加性**：金额按确认份额以整数分最大余数法分配，恒等于来源金额；
  服务次数按份额加权；人数按口径内人员去重，不可跨组织简单相加。
- **跨视角对照**：按 `service_id` 对齐，两侧金额分别列示，绝不相加。

## 快速开始

Python 3.14 标准库，无需在线依赖。

    python -m careorg inspect
    python -m careorg link --perspective responsibility --mode restated \
        --observation-date 2026-12-01 --as-of 2026-12-05
    python -m careorg details --perspective responsibility --mode restated \
        --observation-date 2026-12-01 --as-of 2026-12-05 --level unit

发布与历史版本（`--store` 指定状态目录，默认不落盘）：

    python -m careorg publish --store state --key m1 --label 研究月度 \
        --perspective responsibility --mode as_recorded \
        --observation-date 2026-10-31 --as-of 2026-11-20T00:00:00+08:00
    python -m careorg publish --store state --key op1 --label 运营当前 \
        --perspective responsibility --mode restated \
        --observation-date 2026-12-01 --as-of 2026-12-05T00:00:00+08:00
    python -m careorg history --store state
    python -m careorg compare --store state --left m1 --right op1
    python -m careorg withdraw --store state --withdrawal-id W1 \
        --notice N1 --effective-at 2026-12-10T00:00:00+08:00 --reason 通知撤回

每个查询输出都会附带所选的 `perspective` / `mode` / `org_relationship` /
`observation_date` / `as_of` / `basis_version` / `dataset_id`。

## 测试

    python -m unittest discover -s tests -t . -v
    python support/check_inputs.py
    docker build -t care-organization-dim .
    docker run --rm --network none care-organization-dim

测试覆盖跨期改组、部分交接、链式拆分承接、承接成环、争议份额、份额不足、
层级成环与重复上级、维表修订、资料撤回以及 CLI 端到端流程。

## 目录

    careorg/          维度接收、事实关联、口径发布与查询
    support/          来源资料自检
    tests/            业务规则与命令行测试
    data/             合成来源资料
    docs/             来源资料约定
