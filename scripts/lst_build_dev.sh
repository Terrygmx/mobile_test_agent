#!/bin/bash
# lst_build_dev.sh — 乐速通 dev（测试）环境真机构建
#
# 用法（在 Mobile_test_agent 仓库根目录）：
#   bash scripts/lst_build_dev.sh              # 构建当前 dev 分支源码
#   bash scripts/lst_build_dev.sh install      # 构建并装到 USB 连接的真机
#
# 前提：
#   1. 乐速通源码在 ~/Desktop/workspace/ios（可用 LST_SRC 覆盖），已切到目标分支
#      并 pod install 过（Podfile.lock 有变时必须）；
#   2. Xcode 已登录团队 TSVS7C28BU 的 Apple ID（证书自动管理）；
#   3. 测试机 UDID 在设备列表内（首次构建自动注册）。
#
# 产物：build/lst-device-dev/Build/Products/Debug-iphoneos/nfcPushOnline.app
# 身份：MTA_GIT_COMMIT=<乐速通 HEAD> / MTA_BUILD_ID=local-dev
# 对应 metadata：python -m cli.main repo generate $(LST_SRC) --build local-dev
set -euo pipefail

LST_ENV=dev
# dev 环境的特征 URL：tpark.etcstk.cn/osplatpagetest/ 是 dev 独有的完整 URL
# （dev 与 prod 二进制 strings 差集实测；裸 osplatpagetest 不可用——
# LSTHomeViewController.m:368 硬编码的 URL 任何环境都带）。
# 注意 svipcarddev 不能做指纹：CommonMacro.h 里它只有注释行，dev 的
# BASE_URL 实际是 svipcard/。
VERIFY_PATTERN="tpark.etcstk.cn/osplatpagetest/"

HERE="$(cd "$(dirname "$0")" && pwd)"
MTA_ROOT="$(cd "$HERE/.." && pwd)"

make -C "$MTA_ROOT" lst-build-device "LST_ENV=$LST_ENV"

# 假绿防线：编译成功 ≠ 环境正确，必须验二进制里的 URL（文档 §3.9）
# 注意：验证不能写成 `strings ... | grep -q`——本脚本 set -o pipefail，
# grep -q 命中即退出会给上游 strings 留 SIGPIPE（141），整条管道被误判失败。
# 用进程替换让退出码只属于 grep。
APP="$MTA_ROOT/build/lst-device-$LST_ENV/Build/Products/Debug-iphoneos/nfcPushOnline.app"
if ! grep -q "$VERIFY_PATTERN" <(strings "$APP/nfcPushOnline"); then
    echo "环境验证失败：二进制中未找到 dev 特征 URL: $VERIFY_PATTERN" >&2
    echo "多半是乐速通 CommonMacro.h 条件结构变了，检查 docs/lst_update_playbook.md §3.9" >&2
    exit 1
fi
echo "环境验证通过：dev 特征 URL 已在二进制中 ✓"

if [ "${1:-}" = "install" ]; then
    make -C "$MTA_ROOT" lst-install-device "LST_ENV=$LST_ENV"
fi
