#!/bin/bash
# lst_build_uat.sh — 乐速通 uat（验证）环境真机构建
#
# 用法（在 Mobile_test_agent 仓库根目录）：
#   bash scripts/lst_build_uat.sh              # 构建当前 uat 分支源码
#   bash scripts/lst_build_uat.sh install      # 构建并装到 USB 连接的真机
#
# 注意：uat 的宏组合是嵌套的：TESTING_MODES=1（外层总开关）加
#    VERIFCATION_MODES=1（内层 uat 细分），两个都要。只给一个会静默打成
#    生产包（编译不报错）——这就是为什么本脚本末尾强制 strings 验证。
#
# 前提：
#   1. 乐速通源码在 ~/Desktop/workspace/ios（可用 LST_SRC 覆盖），已切到目标分支
#      并 pod install 过（Podfile.lock 有变时必须）；
#   2. Xcode 已登录团队 TSVS7C28BU 的 Apple ID（证书自动管理）；
#   3. 测试机 UDID 在设备列表内（首次构建自动注册）。
#
# 产物：build/lst-device-uat/Build/Products/Debug-iphoneos/nfcPushOnline.app
# 身份：MTA_GIT_COMMIT=<乐速通 HEAD> / MTA_BUILD_ID=local-uat
# 对应 metadata：python -m cli.main repo generate $(LST_SRC) --build local-uat
set -euo pipefail

LST_ENV=uat
# uat 环境的特征 URL（svipcarduat / svipcard-market-uat），构建后必须在二进制里命中
VERIFY_PATTERN="svipcarduat"

HERE="$(cd "$(dirname "$0")" && pwd)"
MTA_ROOT="$(cd "$HERE/.." && pwd)"

make -C "$MTA_ROOT" lst-build-device "LST_ENV=$LST_ENV"

# 假绿防线：编译成功不等于环境正确，必须验二进制里的 URL（文档 3.9 节）
# 注意：验证不能写成 `strings ... | grep -q`——本脚本 set -o pipefail，
# grep -q 命中即退出会给上游 strings 留 SIGPIPE（141），整条管道被误判失败。
# 用进程替换让退出码只属于 grep。
APP="$MTA_ROOT/build/lst-device-$LST_ENV/Build/Products/Debug-iphoneos/nfcPushOnline.app"
if ! grep -q "$VERIFY_PATTERN" <(strings "$APP/nfcPushOnline"); then
    echo "环境验证失败：二进制中未找到 uat 特征 URL: $VERIFY_PATTERN" >&2
    echo "多半是宏组合不完整（uat 需要 TESTING_MODES + VERIFCATION_MODES 两个），" >&2
    echo "或乐速通 CommonMacro.h 条件结构变了，检查 docs/lst_update_playbook.md 3.9 节" >&2
    exit 1
fi
echo "环境验证通过：uat 特征 URL 已在二进制中 [OK]"
echo "已知例外：LSTHTTPManager.m:32/150 硬编码 svipcard-new/，uat 包的市场请求仍打生产（待 App 同事修复），用例勿覆盖市场模块"

if [ "${1:-}" = "install" ]; then
    make -C "$MTA_ROOT" lst-install-device "LST_ENV=$LST_ENV"
fi
