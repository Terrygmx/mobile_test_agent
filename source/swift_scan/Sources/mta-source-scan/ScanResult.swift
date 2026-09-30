/// ScanResult.swift — mta-source-scan 的输出模型（设计 12.2 / 12.3）
///
/// 字段名与 `source_metadata.json` 的 JSON 键一一对应（snake_case 由
/// CodingKeys 显式映射，不用 keyEncodingStrategy——显式映射在字段改名时
/// 会编译失败，策略转换则是静默换名）。
///
/// `resolution_type`（12.2）是硬契约：只做语法分析，无法唯一解析一律
/// `dynamic` / `unknown`，**不得猜值**。

import Foundation

/// 12.2 resolution_type：四值闭集
public enum ResolutionType: String, Codable {
    /// 字符串字面量
    case literal
    /// 经工程内唯一命名的 `static let` 常量解析得到（两遍扫描常量表）
    case constant
    /// 依赖运行时值（变量、插值、条件表达式）
    case dynamic
    /// 无法判断
    case unknown
}

/// 源码位置
public struct SourceLocation: Codable {
    public let file: String
    public let line: Int

    public init(file: String, line: Int) {
        self.file = file
        self.line = line
    }
}

/// 一个被扫描出的 accessibility 元素（12.3 元素级 schema）
public struct ScannedElement: Codable {
    /// 元素语义 ID（accessibility id 值；unknown 时是带位置的占位 ID）
    public var id: String
    /// 元素类型：`button` / `textfield` / `text` / `cell` / `unknown` ...
    public var type: String
    /// accessibility id 值；无法解析为 nil
    public let accessibilityId: String?
    /// 12.2 四值
    public let resolutionType: ResolutionType
    /// 所在 View / ViewController 类型名（12.1 元素归属）
    public let containerType: String?
    /// 可推断出的元素类型标签（如 SwiftUI `Button { Text("登录") }` 的 label）
    public var label: String?
    public let source: SourceLocation

    enum CodingKeys: String, CodingKey {
        case id
        case type
        case accessibilityId = "accessibility_id"
        case resolutionType = "resolution_type"
        case containerType = "container_type"
        case label
        case source
    }
}

/// Screen 级聚合（12.3 `screens` 数组）
public struct ScannedScreen: Codable {
    public let name: String
    public let elements: [ScannedElement]

    public init(name: String, elements: [ScannedElement]) {
        self.name = name
        self.elements = elements
    }
}

/// 单文件扫描结果（一次 `mta-source-scan <file>` 的输出）
public struct ScanResult: Codable {
    /// 元素所属 Screen 名集合（顶层 screens 输出，兼容旧 schema）
    public let screens: [String]
    /// 归属到 Screen 的元素（12.3 形状：screens[].elements[]）
    public let screenElements: [ScannedScreen]

    enum CodingKeys: String, CodingKey {
        case screens
        case screenElements = "screen_elements"
    }

    public init(screens: [String], screenElements: [ScannedScreen]) {
        self.screens = screens
        self.screenElements = screenElements
    }
}
