# 来源资料约定

组织、归属边、承接交接与服务原始资料。

所有人员、机构、地址、业务事件及数值均为合成，不来自真实企业或个人。UTF-8 编码，日期为 ISO 8601，时间附明确时区。有效区间左闭右开，空结束值为尚无结束记录，不据此推断未来。金额使用整数分，非整数数量用十进制字符串；未知不是零。

legal是法人控制、responsibility是服务责任、finance是财务归集。重述采用指定观察日已生效的通知和交接覆盖，仅改变历史事实的组织解释，不改变服务日期及原机构。金额和服务次数按确认份额分担，剩余未映射保留；人数按口径内人员去重，跨组织不可简单相加。份额超过1或层级成环只隔离相应视角时段，不替其他树作结论。没有法人变化的换名保留org_id。

## organizations

法人身份与名称分离。

- org_id：法人
- name：名称
- revision：名称修订

## sites

站点维度来源。

- site_id：站点
- village：村

## hierarchies

不同视角独立上级边。

- edge_id：边
- perspective：legal/responsibility/finance
- child：下级
- parent：上级
- from_date：开始
- until_date：结束

## assignments

站点服务责任和财务归属独立。

- assignment_id：关系
- site_id：站点
- service：类别
- perspective：归属视角
- org_id：法人
- share：份额
- from_date：开始
- until_date：结束

## successions

空集合不表示全部承接。

- notice_id：通知
- from_org：原法人
- to_org：新法人
- sites：站点集合
- services：类别集合
- share：承接份额
- effective_date：生效日
- received_at：收到

## handovers

通知与实际交接各自成立。

- handover_id：交接
- notice_id：通知
- site_id：站点
- service：类别
- from_date：开始
- until_date：结束

## services

保留实际来源归属。

- service_id：服务
- person_id：人员
- site_id：站点
- service：类别
- occurred_on：发生日
- source_org：来源法人
- amount_fen：金额分
