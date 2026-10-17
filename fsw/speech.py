"""电脑语音代替人说话（决策 158，已在真机上验证过）：语音设备的多轮实测和"试一次"，由电脑朗读句子清单。

只在设备空闲时播（不在一轮中间、且安静了一会儿）：设备已经在听的时候再播唤醒词，会被当成一句话。
播完等设备有反应（收到计时点）再播下一句；连着几次没反应，多半是音量太小或离得太远，提示一句，3 次就停下改由人说。
只有 macOS 自带朗读命令（say），别的系统不提供这个选项。
"""

import shutil
import subprocess
import time

VOICE = "Tingting"  # 中文普通话女声，验证 25 用的就是它
IDLE_SECS = 3.0      # 设备安静这么久才算空闲
WAKE_GAP = 2.5       # 唤醒词播完至少等这么久
WAKE_QUIET = 2.0     # 设备对唤醒词有反应（调试口出声）后，再安静这么久才算回应说完、进入聆听
WAKE_MAX = 10.0      # 最多等这么久，设备一直没输出也照常播句子
ANSWER_SECS = 20.0   # 播完等这么久没收到计时点，算设备没反应
MAX_MISSES = 3


def available():
    return bool(shutil.which("say"))


def say(text, voice=VOICE):
    subprocess.run(["say", "-v", voice, text], check=False, timeout=60)


def drive(speak, next_item, idle, marks, stop, on_hint, wake="", sleep=time.sleep, clock=time.monotonic,
          wake_gap=WAKE_GAP, answer_secs=ANSWER_SECS, max_misses=MAX_MISSES, quiet=None, wake_quiet=WAKE_QUIET, wake_max=WAKE_MAX):
    """一直播到 stop()：idle() 设备空闲；marks() 到现在收到几行计时点（看设备有没有反应）；
    quiet() 调试口距上一行输出几秒：唤醒词后设备常会先回应一句，等它说完（有输出、又安静下来）再播句子，否则句子被它自己的声音盖住。
    返回 "stopped"（外面叫停）或 "gave_up"（连着没反应，已提示改由人说）。"""
    misses = 0
    while not stop():
        if not idle():
            sleep(0.1)
            continue
        before = marks()
        if wake:
            speak(wake)
            t_wake = clock()
            sleep(wake_gap)
            if quiet:
                reacted = False
                while not stop() and clock() - t_wake < wake_max:
                    q = quiet()
                    reacted = reacted or q < clock() - t_wake
                    if reacted and q >= wake_quiet:
                        break
                    sleep(0.1)
        speak(next_item())
        t0 = clock()
        while not stop() and marks() == before and clock() - t0 < answer_secs:
            sleep(0.1)
        if stop():
            break
        if marks() != before:
            if misses:
                on_hint("")
            misses = 0
            while not stop() and not idle():  # 等这一轮做完再播下一句
                sleep(0.1)
            continue
        misses += 1
        if misses >= max_misses:
            on_hint(f"电脑播了 {misses} 次设备都没反应，已停止语音播放：请改由人对设备说，或把电脑音量调大后点「接着测」。")
            return "gave_up"
        on_hint("电脑播完设备没反应：把电脑音量调到 50 以上，或把设备放近一点，码表会再播一次。")
    return "stopped"
