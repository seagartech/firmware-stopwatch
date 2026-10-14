# firmware-stopwatch 固件码表

研发来跑，不写代码的人也能看懂固件哪一步慢、为什么、怎么改。

码表读一份单片机固件的源码，用 AI 找出关键步骤，在这些地方插上计时点，编译烧录后让设备真跑几十轮，最后出一份分段用时报告：哪一步慢、为什么、怎么改、找谁改。第一版支持乐鑫 ESP-IDF 编译的项目。

> 现在还在开发中：装好后能启动，网页和测量功能正在做。

## 在 Mac 上安装

需要苹果芯片的 Mac，并且已经装了 [Homebrew](https://brew.sh)。

1. 装 uv（Python 的安装工具，它会自动准备好合适版本的 Python，不动系统自带的 Python）：

   ```sh
   brew install uv
   ```

2. 拿到代码（仓库地址正式发布时补上；也可以下载压缩包解开）：

   ```sh
   git clone https://github.com/<仓库地址>/firmware-stopwatch.git
   cd firmware-stopwatch
   ```

3. 启动。第一次会自动装好需要的东西，要等几秒：

   ```sh
   uv run fsw --version
   ```

   看到 `firmware-stopwatch 0.1.0` 就是装好了。再运行 `uv run fsw`，会告诉你还有哪些设置没填。

## 在 Windows 上安装

> 还没在 Windows 上实测，下面的步骤可能要调整。

需要 Windows 10 或 11，并且装了 [Git](https://git-scm.com/download/win)。

1. 装 uv：打开 PowerShell，运行

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   装完关掉 PowerShell 再重新打开一个。

2. 拿到代码：

   ```powershell
   git clone https://github.com/<仓库地址>/firmware-stopwatch.git
   cd firmware-stopwatch
   ```

3. 启动：

   ```powershell
   uv run fsw --version
   ```

   看到 `firmware-stopwatch 0.1.0` 就是装好了。再运行 `uv run fsw`，会告诉你还有哪些设置没填。

## 设置和密钥

设置放在你自己电脑上，不在代码文件夹里：

- Mac：`~/.config/firmware-stopwatch/`
- Windows：`%USERPROFILE%\.config\firmware-stopwatch\`

把代码里的 `config.example.toml` 复制到这个文件夹，改名为 `config.toml`，照里面的说明填。以后也可以在码表网页的设置页里改。

密钥不写进 `config.toml`，单独放一个文件，只写密钥本身一行：

- Claude：`claude_key.txt`
- DeepSeek：`deepseek_key.txt`

也可以用环境变量 `ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`，环境变量优先。选"本机已登录的 Claude Code"时不用密钥。

## 参与开发

```sh
uv run pytest                              # 跑全部自动测试
uv run python tools/check_before_upload.py # 上传前检查有没有密钥、本机路径等不该公开的内容
```
