# 回归测试素材

公开素材仅用于离线回归。设备日志只保留 `FSW` 计时协议事件（编号、时间和计数），不含设备状态、网络信息、对话字幕或板级日志。

- `fullflow_multi_sentence/raw.txt`、`fullflow_wake_round/raw.txt`、`run_132839_raw.txt`、`voice_two_face_lines_raw.txt`：裁剪后的计时事件。
- `fullflow_multi_sentence/run-cdd6fed/raw.txt`、`report_main_regression/fsw.txt`：正式纳入版本管理的裁剪事件，独立检出也可回放，避免依赖本机忽略文件。
- `fullflow_multi_sentence/raw-after.txt`：合成的多句回复收尾事件，构造方法见该目录说明。
- 识别与插入方案只保留回归必需的结构化 JSON，移除了原答包装、冗余回答与板级私有名称；中性显示描述是测试示例。
- 公共固件源文件的精确版本和许可见 `fullflow_multi_sentence/README.md` 及 `src/LICENSE`。

上述裁剪数据可以重现既有问题，不代表新模型调用、固件构建或设备验收。其他 `mock` 素材为模拟数据，按各测试的说明使用。
