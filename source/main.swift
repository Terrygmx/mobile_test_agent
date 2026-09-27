// swift_scan.swift — Source Intelligence 编译器（Stage 6）
// 识别模式: .accessibilityIdentifier(<literal>) 与 <modifier>(<literal>) 链
// 非字面量参数 → resolution_type: "unknown"（设计文档 7 节：不硬猜）
// 输出 JSON 到 stdout

import Foundation
import SwiftSyntax
import SwiftParser

struct Element: Codable {
    let id: String
    let type: String
    let accessibilityId: String?
    let resolutionType: String
    let screen: String?
    let source: SourceLoc
}

struct SourceLoc: Codable {
    let file: String
    let line: Int
}

let inputPath = CommandLine.arguments[1]
let sourceFile = Parser.parse(source: try String(contentsOfFile: inputPath, encoding: .utf8))

let visitor = AccessibilityIDVisitor(path: inputPath)
visitor.walk(sourceFile)

// review P0-3：元素按所属 struct 归组，screen 过滤在 reconcile 侧生效
// review R2-0：顶层额外输出 screens 列表（顶层 screen=文件名，与元素级 struct 名
// 本就不同；消费方用元素级 screen 或 screens 列表解析）
let screenName = inputPath.components(separatedBy: "/").last!.replacingOccurrences(of: ".swift", with: "")

let output: [String: Any] = [
    "screen": screenName,
    "screens": visitor.screenNames,
    "elements": visitor.elementsJSON
]

let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)

// MARK: - Visitor

final class AccessibilityIDVisitor: SyntaxVisitor {
    let path: String
    var elementsJSON: [[String: Any]] = []
    // review P0-3：记录当前所属 struct（≈ screen 归属）
    var currentScreen: String?
    // review R2-0：收集全部 struct 名，供顶层 screens 输出
    var screenNames: [String] = []

    init(path: String) {
        self.path = path
        super.init(viewMode: .sourceAccurate)
    }

    override func visit(_ node: StructDeclSyntax) -> SyntaxVisitorContinueKind {
        currentScreen = node.name.text
        if !screenNames.contains(node.name.text) {
            screenNames.append(node.name.text)
        }
        return .visitChildren
    }

    override func visitPost(_ node: StructDeclSyntax) {
        currentScreen = nil
    }

    override func visitPost(_ node: FunctionCallExprSyntax) {
        // 匹配 .accessibilityIdentifier(...)，calledExpression 可能是 MemberAccessExprSyntax
        let name: String
        if let member = node.calledExpression.as(MemberAccessExprSyntax.self) {
            name = member.declName.baseName.text
        } else if let decl = node.calledExpression.as(DeclReferenceExprSyntax.self) {
            name = decl.baseName.text
        } else {
            return
        }
        guard name == "accessibilityIdentifier" else { return }

        guard let arg = node.arguments.first?.expression else { return }
        let location = node.startLocation(converter: SourceLocationConverter(fileName: path, tree: node.root))

        if let str = arg.as(StringLiteralExprSyntax.self) {
            // literal: 解析成功
            elementsJSON.append([
                "id": str.representedLiteralValue ?? "",
                "type": "unknown",
                "accessibilityId": str.representedLiteralValue ?? "",
                "resolution_type": "literal",
                "screen": currentScreen ?? NSNull(),
                "source": ["file": (path as NSString).lastPathComponent, "line": location.line],
            ])
        } else {
            // 非字面量：不猜（设计文档 7 节硬约束）
            // review P2-7：unknown 的 id 带位置，避免多元素重复
            elementsJSON.append([
                "id": "UNKNOWN:\((path as NSString).lastPathComponent):\(location.line)",
                "type": "unknown",
                "accessibilityId": NSNull(),
                "resolution_type": "unknown",
                "screen": currentScreen ?? NSNull(),
                "source": ["file": (path as NSString).lastPathComponent, "line": location.line],
            ])
        }
    }
}

extension ExprSyntax {
    var isAccessibilityIdentifierCall: Bool {
        if let call = self.as(DeclReferenceExprSyntax.self) {
            return call.baseName.text == "accessibilityIdentifier"
        }
        return false
    }
}
