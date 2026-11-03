# 独立计算接口

接口版本 `1.2`（接收显式 `1.0` / `1.1` 请求，统一返回 `1.2`）；算法版本 `0.3-prts`；状态 `source_aligned`。计算口径统一按PRTS，不表示所有效果已编译。算法说明见同目录的 `calculation_algorithm_review.md`。

## 独立窗口与导出

当前默认页为“本局伤害计算”：读取清单、选择干员/技能，固定0防0抗输出 DPH 与技能期间平均 DPS。新增 `operation: session_dps`，支持原 Python/CLI/HTTP 通道及完整导出，时序模型与基础伤害算法分开标记。操作、公式与时序范围见 [本局DPH与DPS](session_dps.md)。旧手工页、按敌人计算及接口服务入口移到“高级计算”，旧接口保持兼容。

双击项目根目录的“启动数值计算器.cmd”，或在 Reader 顶部点击“独立数值计算”。窗口支持属性乘区录入、JSON输入、逐步计算、导出请求与结果、导出Schema；可启动和停止本机HTTP接口。

“效果库”是单独窗口，可查询原文、生效条件线索、叠加上限、来源运算标注、分档与未编译状态。原文收录不能当作规则已可自动执行。

“高级计算”内保留旧版本局页，读取已人工确认的Reader快照并复用基础核心。`operation:session_damage` 缺项/未支持默认返回 `result:null`，显式 `allow_partial:true` 才演算已知贡献。完整约定、面板去重、可运行输入及边界见 [效果规则与本局接入](effect_rule_compiler.md)。

## Python

```python
from reader.calculation import calculate

result = calculate({
    "schema_version": "1.2",
    "attacker": {"atk": {"base": 1000}},
    "defender": {"def": {"base": 300}, "res": {"base": 30}},
    "hit": {"type": "physical"}
})
assert result["hp_damage"] == 700
```

单属性请求可传入 `{"operation":"attribute","stat":"atk","attribute":{"base":500,"direct_mul_delta":[0.5]}}`。核心、CLI与HTTP使用Python标准库；可视化窗口另使用PySide6。

## 数值贡献与可解释输出

原始白值、各阶段数值列表、倍率、贯穿、护盾、基本损伤及攻击间隔等计算操作数可使用裸数或以下对象。`hits`、屏障次数与属性上下限等控制参数继续使用原类型。

```json
{"value":2,"source":{"kind":"skill","name":"真银斩","url":"https://prts.wiki/w/银灰","detail":"攻击力+200%，直接乘算加成率"}}
```

`source.kind` 支持 base、skill、talent、relic、part、map、buff、enemy、module、potential、trust、level、manual、unknown。`name` 必填，`id/url/revision/detail` 可选。来源只解释贡献，不会仅因指定名称而自动激活效果。裸数标为手工输入、未标注来源；显式source=null报错。

返回 `attributes` 中的各属性值及全过程，`white_values` 提供数值摘要；每个结算阶段有 `formula`、`contributors` 和结果。贡献者保留源类型、名字、参数及是否被同组取最高规则选中，未选中项不参与计算。输出保留完整精度与来源；窗口中的长悬浮说明可能截短。

示例：`direct_mul_delta` 中真银斩2与天赋0.25相加；攻击白值833得到2707.25。`hit.attack_scale` / `hit.attack_scale_groups` 用于防抗前攻击力比例，不改白值；防抗后的伤害倍率放 `post_scale_*`，二者不能互换。

## 命名乘区与抗性

属性的 `final_groups`、攻击力比例的 `hit.attack_scale_groups`、伤害的 `hit.post_scale_groups` 使用相同结构。新版默认按最终乘算一般规则逐项乘（multiply）；仅有机制依据时显式使用add/max/min。不同组倍率相乘，同ID规则冲突报错。ID是机制身份，不能仅凭自然语言名称分组。

```json
{"group_id":"arts_damage_bonus", "deltas":[0.2,0.3], "stacking":"add"}
```

上例显式add得到1.5倍；若用新版默认multiply则得到1.56倍。旧 `final_factors` / `post_scale_factors` 只填已解析的独立倍率，不得重复填写相同效果。

显式1.0/1.1请求未填写stacking时保留旧默认add，并在结果中返回 `input_schema_version` 和 `compatibility_notes`。新接入方应使用1.2并显式填写已核实的规则，不依赖旧约定。

法抗属性默认封顶100，再处理 `res_penetration_flat/ratio`；这两个兼容字段**仅表示无视法抗**。普通降低法抗应放进 `defender.res` 对应的属性修饰器，不能移到封顶后。例如140法抗无视20后有效80；140法抗普通降低20后封顶仍100。

`hit.type: element` 结算扣血元素伤害，只使用 `defender.element_res`，不使用 `ep_resistance`；非零防御/法抗贯穿会报错。

元素损伤累积另用以下请求，返回 `buildup_per_hit`、`total_buildup` 与完整中间值，不返回扣血量：

```json
{
  "schema_version":"1.2", "operation":"elemental_buildup", "amount":1000,
  "defender":{"ep_resistance":{"base":30}}, "hits":2,
  "post_scale_groups":[{"group_id":"buildup_bonus","deltas":[0.2,0.3],"stacking":"add"}]
}
```

上例单次1050、两次2100。损伤抵抗通道同样没有抗穿。默认采用详细损伤公式的5%下限；不自动推演爆发、冷却或恢复。

## JSON命令行

```powershell
.\.venv\Scripts\python.exe scripts\calculate.py --input reports\calculation\review_physical.request.json --output result.json
.\.venv\Scripts\python.exe scripts\calculate.py --schema --output calculation.schema.json
```

`--input -` 从标准输入读取UTF-8 JSON，`--output -` 向标准输出返回JSON。成功返回码0；输入错误返回码2和结构化 `error`，不会静默忽略拼错的字段、重复JSON键或非有限数值。

## 本机HTTP

运行“启动数值接口.cmd”，或：

```powershell
.\.venv\Scripts\python.exe -m reader.calculation_api --serve --port 8765
```

地址：`http://127.0.0.1:8765`。

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/health` | 接口/算法版本与来源口径状态 |
| GET | `/v1/schema` | 完整请求JSON Schema |
| POST | `/v1/calculate` | 计算并返回全部中间值 |
| GET | `/v1/effects?search=攻击&limit=20` | 搜索已入库效果；中文查询需URL编码 |
| GET | `/v1/effects/{id}` | 效果原文、条件、参数、来源及执行状态 |
| GET | `/v1/effects/coverage` | 来源覆盖与未解决条目 |
| GET | `/v1/rules/coverage` | 当前活动修订的规则编译覆盖，区分完整/部分/未支持 |
| GET | `/v1/rules/{effect_id}` | 活动来源编译结果、规则证据、条件及未支持部分 |

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8765/v1/calculate -Method Post -ContentType application/json -InFile reports\calculation\review_physical.request.json
```

输入最多1 MB。输出包括版本、来源口径状态、白值、伤害/损伤、吸收结果、算式和贡献来源。调用方可通过 `context` 附加自己的来源ID/备注；该字段不会改变算法或状态。错误以结构化JSON返回，重复键、非有限数值与过深JSON不会静默接受。

单次请求中的 `hits` 表示相同伤害包的段数，不等于自动展开技能全部攻击。不同类型的附伤须分开计算；若要共享护盾/屏障，由调用方传递上一包返回的剩余状态。

供其他Agent执行的Prompt见 `agent_calculation_handoff_prompt.md`。CLI/HTTP均支持上述三个操作；本机窗口提供命名乘区表格、损伤累积页及请求/结果导出。

另支持本局操作 `session_damage`，使用相同CLI与 `POST /v1/calculate`。通用客户端为 `reader/calculation_client.py` 与 `scripts/calculation_client.py`，可选择Python/CLI/HTTP。`GET /v1/effects/{id}` 增加 `active_revision` 与活动来源的 `compilation`；原 `execution_status` 是入库时状态，当前执行能力以编译结果为准。`context` 不改变确认状态或编译选择。
