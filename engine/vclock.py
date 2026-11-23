"""正式版时钟：只使用真实墙钟，不加载任何虚拟时钟文件。"""

import time


def now():
    return time.time()


def real_now():
    return time.time()


def advance_seconds(seconds):
    return None


def set_now(_ts):
    raise RuntimeError("正式版不提供虚拟时钟")


def enabled():
    return False


def install():
    return False
