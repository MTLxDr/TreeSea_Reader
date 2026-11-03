# 效果规则与本局接入

编译器版本 `0.1`，基础接口仍为 `1.2`、算法仍为 `0.3-prts`。实现位于 `reader/effect_rules.py` 与 `reader/session_calculation.py`；基础公式仍全部调用 `reader.calculation.calculate`。

## 已实现的路径

1. 只读数据库活动修订，按游戏ID选择物品档位；同一来源的运算提示与底层参数互作证据，不重复加算。存在数值来源时优先使用该来源，PRTS原文副本不作为另一件物品。档位不唯一、源参数冲突或历史ID失效会明确报错/列缺项。
2. 编译 `char_attribute_add` 与 `char_attribute_mul` 中的攻击、防御、生命、法抗和攻速；识别底层职业、近战/远程选择器。明确无条件且带运算标注的属性提示可作为受限后备。未知选择器、额外参数、最终倍率编码与动态层数不会静默执行。
3. 干员技能/潜能仅解析完整匹配的简单属性句；强力击“下次攻击的攻击力提高至N%”进入防抗前攻击力比例乘区。自然语言中出现百分数不足以确定乘区。多机制技能只编译已知子句，其余仍列未支持。
4. 使用已确认的本局干员、藏品和零件。支持来源明确列出的练度端点、满信赖平值、累计潜能平值与所装备模组的当前等级平值；中间等级/信赖不猜测插值。天赋按精英阶段与潜能选互斥档位；未知天赋条件与模组天赋替换均列缺项。
5. 按 `stage_id + enemy_id + enemy_level` 选择敌人；返回敌我面板、中间值、来源及每条规则是否生效。普通关卡裸参数不能冒充高难度结果；难度非0、未编译关卡规则、条件敌人、缺少独立机制页都会显式阻断完整结果。

职业特性从保存的PRTS原始页面读取，并校验规范换行后的内容哈希。当前只确认“可以远程攻击但此时攻击力降低至80%”这条领主特性在显式近战攻击时不触发降低；远程攻击、其他职业特性及技能覆盖关系继续列为未支持/缺项，不能因属性面板已输入而当作无效果。

每条规则记录 `rule_id/effect_id/subject_id`、来源URL/修订/哈希、适用数据版本、作用对象、条件、属性/伤害类型、阶段、数值单位、`group_id` 和 `stacking`。未知来源版本按 `unknown/unspecified` 原样保留，不虚构适用版本。加算阶段的乘区ID来自PRTS阶段定义；不会按中文名字合组。

## 使用窗口

从 Reader 打开“独立数值计算”，进入“本局状态与效果规则”。从Reader打开时读取内存中的当前快照；独立启动时只读 `data/sessions/current.json`。也可导入Reader导出的本局存档、`session_damage`请求或此前导出的请求/结果文件。导入不会改写、确认或清空库存。

选择干员、关卡、敌人档位、难度、分队、技能状态、伤害类型及本次近战/远程攻击方式，然后点击“组装并核对”。下方JSON仅在勾选“使用下方JSON作为选择与面板输入”后成为权威输入；可点击“从上方选择生成JSON”再补入人工面板。

结果分为生效规则、未生效规则、缺项、未支持和未选中档位。点击“查看逐步算式”在原计算页查看白值、攻击力比例、防抗与扣血过程，来源悬浮仍然可用。导出本局请求与结果会保存完整快照与编译审计。

## 状态和边界

- `complete_for_supported_scope`：本次明确选择的基础单段演算所需信息已足够。它不是完整游戏战斗验证，不证明索敌、命中、攻击前后摇或技能总伤害正确。
- `incomplete`：有缺项或未支持机制。默认 `result:null`，仍返回 `compiled_request` 与完整 `audit`。
- 只有显式 `allow_partial:true` 才尝试演算已知贡献；`status` 仍为 `incomplete`、`partial:true`。调用方必须检查状态，不能只读取嵌套的扣血数值。连攻击基础值或伤害类型也缺失时仍不输出结果。

`confirmed:true` 表示调用方提交的是人工确认快照；服务不会因 `context`、效果名称或 `effect_ids` 擅自确认OCR建议。`effect_ids` 是对已选活动来源的核对约束，不能激活未持有物品。未确认/已移除条目、未确认数量、多件持有但无叠加依据、库存未录完整均显式处理。

模块尚未编译全部零件/藏品、分队、难度、天赋、模组特性、敌人技能和关卡机制。尚无完整技能时间线、概率期望、不同伤害包自动组合或元素爆发。段数只表示调用方明确给出的相同伤害包数量，不从持续时间推算攻击次数。

## 人工面板与重复加成

`selection.operator_panel` 可替代练度端点面板。`stats` 使用数值，`included` 明确列出面板已包含的 `trust/potential/module` 类平值。已包含的平值不会再加；模组特性/天赋替换仍需单独建模，不会因为平值已含就消失。

```json
{"operator_panel":{"stats":{"atk":833,"def":447,"max_hp":2890,"res":10,"attack_speed":100},"included":["trust","potential","module"]}}
```

这只是面板结构示例，不代表当前本局干员。信赖未列入 `included` 时必须有可解析的信赖字段；潜能与模组身份即使平值已含也用于判断天赋档位，未知时仍列缺项。

## Python、CLI与HTTP

完整可运行输入见 `docs/examples/session_strong_strike.request.json`。它是人工构造的验证快照，不能替代用户当前库存。精英0、1级、0信赖、潜能1、无模组银灰明确进行近战攻击，皇帝的恩宠提供近战位单位攻击+15%；1级强力击攻击力比例190%；`ro6_b_1` 的0档战术猎犬防御0：

`攻击白值 = 297 × (1 + 0.15) = 341.55`

`基本伤害 = 341.55 × 1.9 = 648.945`

`物理伤害 = max(0.05 × 648.945, 648.945 − 0) = 648.945`

此例检查基础单段，不验证整个技能循环。具体来源ID、修订和底层参数保留在生成的结果中；不从样例推断其他练度/模组/敌人机制。

```python
import json
from reader.calculation_client import calculate_request
request = json.load(open('docs/examples/session_strong_strike.request.json', encoding='utf-8'))
response = calculate_request(request, 'python')  # 也支持 'cli' 或 'http'
assert response['status'] == 'complete_for_supported_scope'
assert abs(response['result']['hp_damage'] - 648.945) < 1e-8
```

```powershell
.\.venv\Scripts\python.exe scripts\calculation_client.py --input docs\examples\session_strong_strike.request.json --transport python --output result.json
.\.venv\Scripts\python.exe scripts\calculate.py --input docs\examples\session_strong_strike.request.json --output result.json
.\.venv\Scripts\python.exe -m reader.calculation_api --serve --port 8765
.\.venv\Scripts\python.exe scripts\calculation_client.py --input docs\examples\session_strong_strike.request.json --transport http --output result.json
```

HTTP继续使用 `POST /v1/calculate`，新增操作 `session_damage`；`GET /v1/rules/{effect_id}` 返回活动来源编译结果，`GET /v1/rules/coverage` 返回编译覆盖。`GET /v1/effects/{id}` 新增 `active_revision` 和活动修订的 `compilation`，原始入库状态字段保留历史含义。客户端和服务端均限定本机127.0.0.1。没有提供外部宿主项目，因此交付的是通用客户端，不声称已集成某个宿主。

## 验证与覆盖

运行 `scripts/validate_rule_bridge.py` 对照完整样例、职业不匹配、不完整库存、显式部分演算与动态未知机制；每组核对Python/CLI/HTTP响应一致。`scripts/validate_calculation.py` 保留原基础接口回归，`scripts/validate_prts_reference.py` 保留PRTS/ArkDPS参考对照。

`reports/calculation/rule_bridge/coverage.json` 将完整编译、部分编译与未支持来源分列，并按类别统计；`rule_count` 是规则数，来源条目包含不同技能档位和重复证据，不能当作不同游戏效果数量或完整机制覆盖率。原 `reports/effect_coverage.json` 仍是原文收录报告。

数据依据仍为本地保存的来源修订；[PRTS游戏数据基础](https://prts.wiki/w/游戏数据基础)定义基础结算，[PRTS拟造物质编目](https://prts.wiki/w/沉沦者的黑流树海/拟造物质编目)提供藏品原文。未编译部分需要具体机制与参数证据后继续扩展。
## 当前计算入口

默认计算页已改为“本局伤害计算”，固定0防0抗，主要输出 DPH 与技能期间平均 DPS；对应新操作 `session_dps`。上述 `session_damage` 保留在高级页和外部接口。新版复用相同规则来源与伤害核心；操作与时序范围见 [本局DPH与DPS](session_dps.md)。

