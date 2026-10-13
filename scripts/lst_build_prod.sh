#!/bin/bash
# lst_build_prod.sh — 乐速通 prod（生产）环境真机构建
#
# 用法（在 Mobile_test_agent 仓库根目录）：
#   bash scripts/lst_build_prod.sh              # 构建当前生产分支源码
#   bash scripts/lst_build_prod.sh install      # 构建并装到 USB 连接的真机
#
# 注意：prod 环境的定义是两个环境宏都不定义（TESTING_MODES / VERIFCATION_MODES
#    都不给，走 CommonMacro.h 最外层 #else）。所以这个环境最大的风险恰恰是
#    与「忘了配宏的 dev/uat 包」无法从编译结果上区分——本脚本的反向验证
#    （确认二进制里没有测试环境 URL）因此更重要。
#
# 前提：
#   1. 乐速通源码在 ~/Desktop/workspace/ios（可用 LST_SRC 覆盖），已切到目标分支
#      并 pod install 过（Podfile.lock 有变时必须）；
#   2. Xcode 已登录团队 TSVS7C28BU 的 Apple ID（证书自动管理）；
#   3. 测试机 UDID 在设备列表内（首次构建自动注册）。
#
# 产物：build/lst-device-prod/Build/Products/Debug-iphoneos/nfcPushOnline.app
# 身份：MTA_GIT_COMMIT=<乐速通 HEAD> / MTA_BUILD_ID=local-prod
# 对应 metadata：python -m cli.main repo generate $(LST_SRC) --build local-prod
set -euo pipefail

LST_ENV=prod
# prod 用双向验证：
# 正向——www.bjetc.cn 是 prod 独有域名（dev/uat 走 tpark.etcstk.cn），
#        prod 二进制必须命中；
# 反向——svipcarduat（uat 特征段，只在 CommonMacro.h uat 条件区内）不应出现。
# osplatpagetest 不能做反向指纹——LSTHomeViewController.m:368 无条件硬编码了
# 含它的内网 URL，任何环境的二进制都会带（App 侧问题，已另报备）；
# svipcarddev 同理不可用——它在 CommonMacro.h 里只有注释行，任何构建都不会有。
POSITIVE_PATTERN="www.bjetc.cn"
NEGATIVE_PATTERNS="svipcarduat"

HERE="$(cd "$(dirname "$0")" && pwd)"
MTA_ROOT="$(cd "$HERE/.." && pwd)"

make -C "$MTA_ROOT" lst-build-device "LST_ENV=$LST_ENV"

# 假绿防线（反向）：prod 包里出现测试环境 URL = 宏配置泄漏，必须拦下
# 注意：验证不能写成 `strings ... | grep -q`——本脚本 set -o pipefail，
# grep -q 命中即退出会给上游 strings 留 SIGPIPE（141），整条管道被误判失败。
# 用进程替换让退出码只属于 grep。
APP="$MTA_ROOT/build/lst-device-$LST_ENV/Build/Products/Debug-iphoneos/nfcPushOnline.app"
BIN="$APP/nfcPushOnline"
if ! grep -q "$POSITIVE_PATTERN" <(strings "$BIN"); then
    echo "环境验证失败：prod 包二进制中未找到 prod 特征域名: $POSITIVE_PATTERN" >&2
    echo "宏可能没配对（比如误配了测试宏），检查 docs/lst_update_playbook.md 3.9 节" >&2
    exit 1
fi
for pat in $NEGATIVE_PATTERNS; do
    if grep -q "$pat" <(strings "$BIN"); then
        echo "环境验证失败：prod 包二进制中出现了测试环境 URL: $pat" >&2
        echo "宏可能没清干净，或乐速通 CommonMacro.h 条件结构变了，" >&2
        echo "检查 docs/lst_update_playbook.md 3.9 节" >&2
        exit 1
    fi
done
echo "环境验证通过：prod 包无任何测试环境 URL [OK]"
echo "警告：这是生产环境包，会请求真实生产接口、产生真实业务行为（如 ETC 业务数据），仅在确认要测生产时使用"

if [ "${1:-}" = "install" ]; then
    make -C "$MTA_ROOT" lst-install-device "LST_ENV=$LST_ENV"
fi
