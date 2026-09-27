# Mountain Search UAV

登山用户记录与无人机搜索任务系统。包含手机端、地面站、无人机端、MATLAB 三模式山地仿真及实验资料。

当前源码版本：UAV-SEARCH-20260928。

## 内容

- `code/`：三个组件源码与测试。
- `simulation/`：MATLAB 脚本、地形、轨迹、可编辑图和动画。
- `experiments/`：本地传输测试结果与户外视频。
- `docs/技术说明.pdf`、`docs/技术说明.docx`：完整技术说明。
- `records/`：验证结果、来源记录与文件校验。

Mode 1 根据规划路线，以 4 km/h 自动估算结束时间；Mode 2 检查定位更新超时；Mode 3 的 SOS 经过操作员联系核实和确认后才创建搜索事件。事件选定后生成航线，由操作员审阅和派发。

## 复核

在档案根目录执行：

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-verification.txt
python scripts/prepare_local.py
cd code/FYP_alin1_SmartUAVRescueSystem_Mobile_APP-main
npm ci
cd ../..
python scripts/verify.py --output local-results/verification
```

MATLAB 在 `simulation/` 目录运行 `run_all('simulate')`。仅重画论文图使用 `run_all('paper')`。动画使用 `run_all('video')`。

数据库、消息服务和机载运行设置见技术说明；外部飞行程序使用固定提交，配置步骤见 `docs/飞行环境配置.txt`。

文件完整性：

```
python scripts/check_archive.py
```

## 来源

步行速度参考 Ordnance Survey《Map Reading》的步行估时说明；完整链接见 `docs/资料来源.txt`。高程为 Mapzen / Tilezen Skadi N22E114，路线和 GPS 历史为构造的仿真输入。各源码组件的原许可和版权说明见 `LICENSES/`。
