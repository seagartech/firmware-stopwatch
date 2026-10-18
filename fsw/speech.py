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
WAKE_MAX = 15.0      # 唤醒词播完最多等这么久设备回应完，等不到也照常播句子
AFTER_RESET = 0.5    # 设备回应完（打出一轮结束标记）后再隔一下才播
ANSWER_SECS = 20.0   # 播完等这么久没收到计时点，算设备没反应
MAX_MISSES = 3


def available():
    return bool(shutil.which("say"))


def say(text, voice=VOICE):
    subprocess.run(["say", "-v", voice, text], check=False, timeout=60)


def drive(speak, next_item, idle, marks, stop, on_hint, wake="", sleep=time.sleep, clock=time.monotonic,
          answer_secs=ANSWER_SECS, max_misses=MAX_MISSES, resets=None, wake_max=WAKE_MAX, after_reset=AFTER_RESET):
    """一直播到 stop()：idle() 设备空闲；marks() 到现在收到几行计时点（看设备有没有反应）；
    resets() 到现在收到几个一轮结束标记（FSW,RESET，插在设备说完、回到聆听的地方）：唤醒词后设备常会先回应一句，
    要等它说完再播句子，否则句子被它自己的声音盖住。不能看调试口安静：设备说话期间日志也会停好几秒。
    返回 "stopped"（外面叫停）或 "gave_up"（连着没反应，已提示改由人说）。"""
    misses, need_wake = 0, bool(wake)
    while not stop():
        if not idle():
            sleep(0.1)
            continue
        if need_wake:  # 只在开头和设备没反应（多半回到待机）时播：设备说完会接着听，每句都带唤醒词会被当成一句话
            speak(wake)
            t_wake, r0 = clock(), resets() if resets else 0
            if resets:
                while not stop() and resets() == r0 and clock() - t_wake < wake_max:
                    sleep(0.1)
                if resets() != r0:
                    sleep(after_reset)
            else:
                sleep(2.5)
            need_wake = False
        before = marks()
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
        need_wake = bool(wake)
        if misses >= max_misses:
            on_hint(f"电脑播了 {misses} 次设备都没反应，已停止语音播放：请改由人对设备说，或把电脑音量调大后点「接着测」。")
            return "gave_up"
        on_hint("电脑播完设备没反应：把电脑音量调到 50 以上，或把设备放近一点，码表会再播一次。")
    return "stopped"
