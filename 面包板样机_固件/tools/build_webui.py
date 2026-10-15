#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_webui.py —— 把 webui/index.html 打包成固件可用的 C 头文件

为什么用「原始字符串字面量」而不是十六进制字节数组：
    R"(...)" 是 C++11 的原始字符串，里面的双引号、反斜杠、换行全部原样保留，
    一个字符占一个字节，不用手工转义，也不会出现「漏转义一个引号导致编译炸」。
    ESP32 的字符串常量本来就在 flash 里且可直接寻址，所以不需要 PROGMEM 技巧。

用法：
    python tools/build_webui.py
在 firmware 根目录执行即可（脚本会自己定位路径）。
产出：include/webui_asset.h

★ 每次改完 index.html 都要重跑这个脚本，否则设备端发出去的还是旧页面。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "webui", "index.html")
DST = os.path.join(ROOT, "include", "webui_asset.h")

# 原始字符串的结束标记。必须确保 index.html 里不出现这一串。
DELIM = "__VIBUI__"

HEADER = """#pragma once
#include <Arduino.h>

// ============================================================
//  ⚠ 本文件由 tools/build_webui.py 自动生成 —— 不要手工编辑！
//     源文件：webui/index.html
//     重新生成：python tools/build_webui.py
//
//  想改界面又不想重编译固件？把 index.html 拷到 TF 卡根目录，
//  设备会优先用卡上的 /ui.html（见 src/netui.cpp 的 serveAsset）。
// ============================================================

static const char WEBUI_HTML[] =
R"{D}({HTML}){D}";

static const uint32_t WEBUI_HTML_LEN = sizeof(WEBUI_HTML) - 1;
"""


def main():
    if not os.path.isfile(SRC):
        print("[x] 找不到源文件: %s" % SRC)
        return 1

    with open(SRC, "r", encoding="utf-8", newline="") as f:
        html = f.read()

    # 统一成 \n（避免 CRLF 混进来，虽然对浏览器无影响，但省得占体积）
    html = html.replace("\r\n", "\n").replace("\r", "\n")

    if DELIM in html:
        print("[x] index.html 里出现了原始字符串结束标记 %s，请换一个标记" % DELIM)
        return 1

    out = HEADER.format(D=DELIM, HTML=html)

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)

    raw = len(html.encode("utf-8"))
    print("[ok] %s -> %s" % (os.path.relpath(SRC, ROOT), os.path.relpath(DST, ROOT)))
    print("     HTML %d 字符 / %d 字节（UTF-8），生成的 .h 约 %.1f KB"
          % (len(html), raw, os.path.getsize(DST) / 1024.0))
    print("     Flash 占用参考：约占 ESP32-S3 8MB flash 的 %.2f%%" % (raw / (8 * 1024 * 1024) * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
