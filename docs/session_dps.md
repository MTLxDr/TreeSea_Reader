# 本局 DPH 与技能期间平均 DPS

## 使用

Reader 清单页核对干员、藏品、零件，确认已扫描完整后，打开顶部“独立数值计算”。默认进入“本局伤害计算”：

1. 点击“读取Reader当前清单”，或导入本局 JSON。
2. 选择干员、技能和技能等级；等级、信赖、潜能、模组可直接在左侧补录。
3. 点击“计算 DPH / DPS”。右侧两张卡显示单段命中伤害与技能期间平均伤害/秒。下方显示攻速、攻击间隔、时长、攻击次数和除法算式。
4. 展开“计算过程与来源”查看各阶段算式，悬停结果查看贡献者；“效果与缺项”列出藏品、技能、天赋及当前缺少的规则。“导出本局计算”保存请求和完整结果。

目标固定为单目标 0 防御、0 法抗、0 元素抗性，无需选择敌人。主卡不显示总伤，总伤保留为 DPS 的中间值。原手工计算、按敌人计算、JSON 和本机接口入口位于“高级计算”。导入旧计算请求会显式打开高级 JSON，左侧表单停用；导入普通本局存档仍使用表单。

读取或修改输入立即清除旧结果。计算页补录只修改本次请求，不改写 Reader 存档，不自动确认识别条目。

## 计算口径

基础伤害仍使用 `reader/calculation.py` 的 PRTS 结算顺序和来源记录；新增时序模型 `1.0-uniform`。公式为：

```text
修正间隔 T0 = (基础攻击间隔 + 间隔加算) × 间隔倍率
实际攻击间隔 T = T0 / (clamp(攻击速度, 10, 600) / 100)
命中时刻 t = 首次命中时间 + k × T，k 为非负整数，0 ≤ t < 技能持续时间
技能期间总伤害 = DPH × 每次攻击段数 × 技能期间攻击次数
技能期间平均 DPS = 技能期间总伤害 / 技能持续时间
```

攻击速度属性默认100，保留属性结算结果，仅在间隔换算时限制至10–600；属性本身仍使用 PRTS 的最低20限制。攻击速度无单位，间隔和时长单位为秒。攻速公式来源：[PRTS 游戏数据基础](https://prts.wiki/w/游戏数据基础#攻速公式)。

模型默认首击0秒、目标持续可攻击、不进行帧对齐。这些是可编辑的计算假设，不是动画测量结果。可选择30帧四舍五入近似，不能据此覆盖所有干员的前摇、后摇与技能特殊时序。依据：[PRTS 帧对齐机制](https://prts.wiki/w/作战机制/sandbox#帧对齐机制对攻速的影响)。结束时刻的命中不计入；连射各段暂按同一时刻命中。

例：精英0、40级、0信赖、潜能1玫兰莎，技能1等级7，无模组，持有皇帝的恩宠，近战物理攻击。源面板攻击583，技能+50%，藏品+15%，时长20秒，间隔1.5秒：`DPH=583×1.15×1.5=1005.675`；0至19.5秒共14次；`DPS=1005.675×14÷20=703.9725`。这是公开资料与上述模型假设的计算结果，未做游戏动画逐帧对照。

## 外部调用

Python 使用同一调度器，CLI/HTTP 无需调用另一套公式：

```python
from reader.calculation_client import calculate_request
request = {"schema_version": "1.2", "operation": "session_dps",
           "session": confirmed_reader_snapshot,
           "selection": {"operator_id": "char_208_melan", "damage_type": "physical",
                         "attack_mode": "melee", "model": {"first_hit": 0, "frame_align": False}}}
result = calculate_request(request)  # 也可指定 transport='cli' 或 'http'
```

`session` 使用 Reader 已确认清单。`selection.operator_fields` 可补录本次干员练度；`selection.model` 可指定 `duration`、`base_interval`、`interval_add`、`interval_factor`、`first_hit`、`hits_per_attack`、`frame_align`。时长>0且≤36000秒；段数1–32；累计命中不超过100万。其他字段见 `calculation.schema.json`。

示例请求：`docs/examples/session_dps.request.json`，它是验证样例，不是用户本局数据。

```powershell
.\TreeSeaReaderCLI.exe --tool calculate --input docs\examples\session_dps.request.json --output result.json
```

输出 `result.dph` 与 `result.skill_average_dps`；`window_result` 保留次数、间隔、中间总伤和算式；`dph_result` 保留白值及来源；`audit` 和 `timing_missing` 列出缺项。`timing_model_version` 与原基础算法版本分开。旧 `session_damage` 仍按原约定工作。

## 限制与待完成

- 当前只支持已编译效果下的普通均匀攻击、明确连射段数与间隔修正。不含弹药、固定频率伤害、持续递变、额外召唤物、溅射、多目标和完整技能充能循环。
- 瞬发、永续、切换技能或没有明确有限时长的技能不套用此模型；人工填时长不能补成已支持。伤害可算而时序未支持时只显示 DPH。
- 未知或未编译效果默认阻止完整输出；用户主动勾选部分结果才显示已支持贡献。技能本身时序不明时，勾选部分结果仍不输出 DPS。敌人/关卡效果不计入0防0抗目标，分队/难度默认不使用额外修正。
- 本次五组检查覆盖异常/极值输入、终点和帧边界、多段与攻速上限、真实来源算例、旧接口及Python/CLI/HTTP一致性、界面导入修改导出。界面已离屏渲染检查；未做真实游戏中的技能逐帧测量。
