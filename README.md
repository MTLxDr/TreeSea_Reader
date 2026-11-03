# 树海 Reader

树海 Reader 是一个**由 AI 生成的非官方项目**，面向《明日方舟》集成战略「沉沦者的黑流树海」。它在 Windows 上读取指定游戏窗口的可见画面，用于记录探索进度、查询游戏资料和估算本局伤害。截图在本地处理。

## 下载和启动

1. 打开 [GitHub Releases](https://github.com/MTLxDr/TreeSea_Reader/releases)，下载最新发布页中的 Windows x64 便携 ZIP。
2. 在 Windows x64 上完整解压 ZIP，运行解压目录中的 `TreeSeaReader.exe`。便携包自带 Python 运行环境、识别模型和资料数据库。
3. 在 Reader 顶栏选择游戏窗口，再开启「实时跟踪」。地图、藏品和干员清单需要在游戏中显示后才能读取，识别结果可手动校正。

如需校验下载文件，在 PowerShell 中运行以下命令，将结果与同一 Release 的 `.sha256` 文件比较。

```powershell
Get-FileHash .\TreeSeaReader-ver1.0-recognition-20261103-win64.zip -Algorithm SHA256
```

## 功能

- **探索记录** · 识别地图、资源、藏品、零件和干员，跟踪进入节点和已遇事件，支持手动校正。[识别更新说明](docs/recognition_upgrade_20261103.md)
- **探索查询** · 查看节点概率、事件池、剩余藏品池和关卡路线。
- **数值计算** · 查询效果资料，查看伤害计算的中间值。根据本局清单估算单次伤害和技能期间平均每秒伤害（DPS）。
- **命令行与本地接口** · 使用 `TreeSeaReaderCLI.exe` 调用计算、识别和更新功能。详见 [启动与封装](docs/packaging.md)和[计算接口](docs/calculation_api.md)。

## 数据来源

- [PRTS Wiki](https://prts.wiki/) · 藏品、零件、干员技能和模组、敌人与关卡规则等资料。使用条件见 [PRTS 版权说明](https://prts.wiki/w/PRTS:%E7%89%88%E6%9D%83)。
- [路标档案馆](https://www.lubiao.wiki/) · 树海地图构型、节点观测统计、事件与关卡等探索资料。

游戏原文及图像的权利归原权利人。项目的识别、计算和估算结果由 Reader 生成。

## 源码与开发

- [`reader/`](reader/) · 桌面界面、窗口捕获、识别、概率与伤害计算。
- [`scripts/`](scripts/) · 数据采集、构建和验证脚本。
- [`tests/`](tests/) · 单元测试和回归测试。
- [`WORKLOG.md`](WORKLOG.md) · 版本修改记录。

公开源码仓库包含代码和依赖清单。完整运行、便携版构建及部分回归测试还依赖资料数据库、素材和测试图片。直接使用程序时请下载便携包。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_calculation.py
```

## 已知限制

- 节点概率依据现有地图观测估算。缺少事件频数时，事件池按候选等权展示。
- 低置信度图标需手动核对，未显示的游戏信息无法通过截图读取。
- 伤害估算仅应用已实现的效果规则。计算结果会列出未处理的效果或缺失参数。
