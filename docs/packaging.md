# 启动与便携封装

## 2026-11-01 实时探索更新（r4）

本版同步实时采集、居民历史约束、事件池、剩余藏品池和独立关卡查询；便携运行环境新增windows-capture 2.0.1，包含data/exploration公开来源及SQLite。来源快照取得于2026-10-30。操作见 [实时探索说明](live_exploration.md)。r3等旧包保留。

## 日常使用

- 原项目目录：双击 `TreeSeaReader.exe`，使用原项目环境与已有本局数据。
- 便携版：完整解压 `dist/TreeSeaReader-ver1.0-win64.zip` 后，双击文件夹中的 `TreeSeaReader.exe`。它使用随包运行环境，不需要另装Python、Qt或OCR模型。
- 主界面保留地图、节点概率和库存/干员页，顶部可打开效果库与独立数值计算；计算窗口包含本局规则接入、逐步算式、来源说明、JSON导出和本机接口启停。

四个原中文CMD仍可使用，统一转到相同启动器：主程序、计算器、效果库、数值接口。需要把整个便携文件夹放在一起，单独复制EXE无法包含数据库和OCR运行环境。目标为Windows x64，EXE启动器使用Windows的.NET Framework 4.x。

便携目录与原项目数据分开：原项目的本局存档、截图、日志和设置没有打进发布包。便携版首次启动生成自己的 `data/sessions` 等文件；可通过本局导入功能继续使用已有JSON存档。

Reader、从 Reader 打开的效果库和计算器均使用普通窗口，持续置顶功能已移除。旧设置中的 `always_on_top` 会被忽略，无需删除整个设置文件，原热键和其他选项继续保留。“读取完成后显示结果”开关仍有效：开启时，每次手动识别完成仅恢复窗口并请求前台一次；之后允许正常切换窗口，没有保持前台的计时器。显式启动程序或使用显示热键仍会正常显示窗口。

更新后关闭正在运行的旧 Reader，再运行新版本，避免单实例入口仍将请求交给旧进程。当前保留 TAB 长按/切换方式，持续置顶保持移除；计算器默认切到本局DPH/DPS流程，基础伤害公式、概率和数据库不变。

## TAB 节点概率轮播与连续地图

左侧“TAB操作”提供两种方式，选择会保存，旧设置或无效值默认使用长按。仅在 Reader 获得前台焦点且处于“主地图”或“原图与叠加”页时生效：

- 长按显示：按住 TAB 超过 1 秒开启，松开停止。短按 TAB 仍移到下一个控件。
- 按键切换：按一次 TAB 立即开启，松开后继续轮播，再按一次停止。自动重复按键不会反复开关。

两种方式均先轮播诡秘类，再轮播凶戾类；每次只显示一种具体节点，所有适用位置同时显示该类型及各自概率。每种停留 2 秒，亮度每 0.3 秒变化一次。停止后当前类型常亮保留 3 秒，再恢复原图标和标签。保留期间重新开启会取消旧计时；切换操作方式、切换页签、失焦、最小化或开始识别立即清除，不自动恢复轮播。顶部说明按当前操作方式更新。

预测图标直接按素材清单的中文名称查找，兼容“失与得”等与内部编号不同的节点；预测期间隐藏对应位置的旧图标与未知标签。原图中已包含的未知图标和文字由临时不透明背景遮盖，闪烁时背景不变暗，结束后完整移除。缺图时仍显示具体名称；“未知的诡秘／凶戾”只表示观察到的节点分类，不作为预测候选。

例如轮播到“秘境行商”，标出的每个百分比表示“这个节点是秘境行商”的概率，不是把秘境行商在全图的位置分配为总共 100%。显示约整方式与节点详情一致，内部计算保留原概率。当前规则见根目录 `FACTS.md`。

连续识别的楼层、构型、标准网格节点位置、连线、起点与出口相同时，视作同一张地图；缩放和平移导致的像素坐标变化不另建地图。人工修正优先，之前可靠识别的已揭示节点不会被后一次漏识别覆盖；新的已揭示节点正常加入本层已确认数量。同一位置只计一次，不把重复截图作为新的路标样本。

同图的节点观察、距起点距离、统计频数及数量约束均未变化时复用概率；节点揭示、人工修改、约束变化或重新加载的新统计会触发重算。轮播使用当前计算结果。相同构型在不同局内无法自动区分，开始另一局前点击“新建本局”；清空操作仍可撤销。

连续两次可靠识别若给出冲突类型，节点显示橙色边框，悬停或点击可查看两种识别结果；概率暂时停止。点击该节点，选择正确类型后点“设为此类型并锁定”，即可恢复计算。人工锁定结果优先于后续 OCR。

### 限制与待验证

- 窗口行为完成四组 Windows 原生检查：默认及旧配置不置顶、地图/清单识别完成只调用一次唤起、效果库/计算器为普通窗口、关闭自动显示时不强制弹出；还确认了原生置顶标志被清除。当前桌面拒绝后台测试进程的前台激活请求，交互工具也返回 `failed to activate captured window`；切窗不抢回的逻辑通过离屏 Qt 检查，真实游戏中的前台切换尚未实测。记录见 `reports/window_presentation/`。
- 概率仍为路标经验频率按本层数量范围条件化的结果，没有取得游戏随机生成代码。缓存不会增加其统计精度。
- 仅未知的凶戾、未知的诡秘中具有适用统计的节点参与轮播。仅模板位置的未识别节点、右侧小地图缺少适用统计时明确提示，不补造概率；零概率类型不参与轮播。
- 两种 TAB 方式完成五组离屏 Qt 检查：长按和松开保留、切换及重复按键、设置保存/无效值回退、失焦/切页/最小化、重新开启/概率更新/空输入。图标及分类顺序沿用此前已通过的显示检查。没有做真实键盘操作检查；TAB 只作用于 Reader，不向游戏发送按键。

本次 TAB 检查命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tab_modes.py -v`。界面检查使用 `QT_QPA_PLATFORM=offscreen`，临时存档与个人存档分开；沙箱中可把 `TMP` 与 `TEMP` 指向可写的临时目录。此前图标、同图合并与概率缓存检查保留在 `tests/test_probability_overlay.py`，统计公式未改动。当前记录见 `reports/tab_modes/`。

窗口专项检查：`.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_window_presentation.py -v`。默认离屏运行；原生检查设 `QT_QPA_PLATFORM=windows`，若 Windows 拒绝后台激活，前台切换项明确跳过；需要实际点击的检查另设 `TREESEA_INTERACTIVE_WINDOW_TEST=1`，只操作临时窗口。

## CMD故障与修复

旧主入口实际复现了 `ENCODING is not recognized`，并错误提示项目环境不存在，导致没有创建窗口。原启动文件为LF换行，且各入口错误处理不一致。

现在所有CMD统一为ASCII内容、Windows CRLF换行；路径完整引用，参数继续转发。统一Python入口为 `launch_reader.py`，包含所有工具的启动诊断和报错。EXE入口可处理中文、空格路径，命令行版保留UTF-8标准输入输出。

GUI按程序所在目录定位资源；CLI保留调用者的工作目录，输入/输出相对路径按调用目录解析，避免外部程序从其他目录调用时找错文件。

日志：`data/logs/startup.log`；实际GUI窗口状态：`data/logs/launch_reader.json`、`launch_calculator.json`、`launch_effects.json`。原生引导失败的诊断为 `bootstrap_error.txt`。日志只在本机保存。

## 命令行与Agent接入

```powershell
.\TreeSeaReaderCLI.exe --tool calculate --input request.json --output result.json
.\TreeSeaReaderCLI.exe --tool api --port 8765
.\TreeSeaReaderCLI.exe --tool recognize map.png --output recognized.json
.\TreeSeaReaderCLI.exe --tool update --check-only
.\TreeSeaReaderCLI.exe --self-check --with-ocr --report diagnostic.json
```

`--tool calculate` 同时支持手工请求和 `session_damage` 本局请求；接口算法没有另复制一套公式。API仅监听127.0.0.1，正常运行持续服务，Ctrl+C停止。

## 构建与验证

源码构建：

```powershell
.\.venv\Scripts\python.exe scripts\build_launchers.py
.\.venv\Scripts\python.exe scripts\build_portable.py --output "dist\新便携版"
```

构建器使用与当前环境匹配的Python官方嵌入式发行版，按依赖元数据收集实际运行依赖，包含系统环境中被项目使用的库；当前只需Qt Essentials，不打包未使用的Qt Addons。OCR模型随包保留，运行时不依赖联网下载模型。

已有输出目录不会被构建器覆盖。发布包保留依赖元数据、许可证、公开来源原文及数据库快照，`portable_manifest.json` 记录运行环境版本和文件哈希。打包不会重写原数据库或用户会话。

`scripts/validate_launchers.py` 从其他工作目录实际调用CMD和EXE，检查Windows原生窗口可见性、中文JSON输入、带中文和空格的文件名、API健康/计算/正常退出。便携版另做真实地图OCR。完整结果在 `reports/packaging/`，其余数值和识别边界仍以对应功能文档为准。

运行环境依据：[Python官方Windows嵌入式发行版说明](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。
## 2026-10-30 本局计算更新（r3）

本次发布 `dist/TreeSeaReader-20261030-r3-win64.zip`，便携目录同步更新；旧压缩包保留。默认计算页改为读取清单、选择干员与技能，固定0防0抗显示DPH及技能期间平均DPS，并可查看攻速/间隔/时长/次数与来源。使用方法见 [本局计算](session_dps.md)。旧API和TAB功能保持兼容；验证记录在 `reports/session_dps/`。该包未包含此后提出的后台实时截图、事件池与藏品池查询需求。


r5已独立完成171项源码/便携验收并保留归档。本次ver 1.0说明：delivery_r6_20261102.md；打包核对：reports/revision_r6/delivery.json。
