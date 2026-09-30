import Foundation

/// 附录 A4（Task 2.3 / P1-06）：App 侧 reset hook。
///
/// 契约：以 `-UITestReset` 启动参数启动时，在 UI 展示前清理
/// UserDefaults / 缓存 / 登录态（本 App 项）/ Keychain（本 App 项）。
/// 仅 DEBUG 构建生效（调用点在 LoginView.init 的 #if DEBUG 内），
/// release 包不含清理逻辑。
///
/// 与 REST/REINSTALL 的分工（设计 11.1 注）：Keychain 不一定被 REINSTALL
/// 清掉，认证类状态以本 hook（App 自己清）为准。
enum MTAResetHook {
    static func performReset() {
        NSLog("MTA_RESET_HOOK executed (bundle=%@)", Bundle.main.bundleIdentifier ?? "?")
        // 1. UserDefaults：清 App 自己的 domain（不碰系统 domain）
        if let domain = Bundle.main.bundleIdentifier {
            UserDefaults.standard.removePersistentDomain(forName: domain)
        }
        UserDefaults.standard.synchronize()

        // 2. Keychain：清本 App 的通用密码项（kSecClassGenericPassword）。
        //    其余 Keychain class（证书/密钥）不属于登录态契约，不动。
        SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
        ] as CFDictionary)

        // 3. 缓存：URLCache 全清（App 无自建缓存层，有则在此追加）
        URLCache.shared.removeAllCachedResponses()

        // 4. 登录态：isLoggedIn 是内存 @State，进程重启即复位；
        //    将来引入持久化会话（如 Keychain 的 session token）时在此补删。
    }
}
