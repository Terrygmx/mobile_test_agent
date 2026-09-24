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

let output: [String: Any] = [
    "screen": inputPath.components(separatedBy: "/").last!.replacingOccurrences(of: ".swift", with: ""),
    "elements": visitor.elementsJSON
]

let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)

// MARK: - Visitor

final class AccessibilityIDVisitor: SyntaxVisitor {
    let path: String
    var elementsJSON: [[String: Any]] = []

    init(path: String) {
        self.path = path
        super.init(viewMode: .sourceAccurate)
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
                "source": ["file": (path as NSString).lastPathComponent, "line": location.line],
            ])
        } else {
            // 非字面量：不猜（设计文档 7 节硬约束）
            elementsJSON.append([
                "id": "UNKNOWN",
                "type": "unknown",
                "accessibilityId": NSNull(),
                "resolution_type": "unknown",
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
