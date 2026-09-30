/// UIKitVisitor.swift — UIKit identifier/label 识别（设计 12.1）
///
/// 识别模式（12.1）：`x.accessibilityIdentifier = "…"` 与
/// `x.accessibilityLabel = "…"` 赋值。SwiftSyntax 只做语法分析——这里
/// **不做**表达式求值：右侧字面量 → literal；右侧引用常量 → 查表 → constant；
/// 其余 → dynamic / unknown（不猜）。
///
/// 标签语义约定：identifier 与 label 各自独立产出条目（同一 receiver 的两次
/// 赋值合并成一条元素：identifier 为主，label 为补充）。

import Foundation
import SwiftSyntax
import SwiftParser

public final class UIKitVisitor: SyntaxVisitor {
    private let filePath: String
    private let constants: ConstantTable
    private var elements: [ScannedElement] = []
    private var containerStack: [String] = []
    private(set) var screenNames: [String] = []
    /// receiver 文本 → 索引（同 receiver 的 label/identifier 合并）
    private var byReceiver: [String: Int] = [:]
    /// receiver → 最近一次 label 值（label 可能在 identifier 之后出现）
    private var labelsByReceiver: [String: String] = [:]

    public init(filePath: String, constants: ConstantTable) {
        self.filePath = filePath
        self.constants = constants
        super.init(viewMode: .sourceAccurate)
    }

    // MARK: - 容器归属（12.1：View / ViewController 类型）

    public override func visit(_ node: StructDeclSyntax) -> SyntaxVisitorContinueKind {
        containerStack.append(node.name.text)
        return .visitChildren
    }

    public override func visitPost(_ node: StructDeclSyntax) {
        _ = containerStack.popLast()
    }

    public override func visit(_ node: ClassDeclSyntax) -> SyntaxVisitorContinueKind {
        containerStack.append(node.name.text)
        return .visitChildren
    }

    public override func visitPost(_ node: ClassDeclSyntax) {
        _ = containerStack.popLast()
    }

    // MARK: - 赋值识别

    /// 新版 SwiftSyntax 实锤（debugDescription dump）：`x.p = "v"` 折叠成
    /// `SequenceExprSyntax > ExprListSyntax: [MemberAccess, AssignmentExprSyntax,
    /// StringLiteral]`——**不是** InfixOperatorExpr（那个 visit 根本不触发）。
    /// 判定：SequenceExpr 的第三个子是 AssignmentExprSyntax（`=`）。
    public override func visit(_ node: SequenceExprSyntax) -> SyntaxVisitorContinueKind {
        // ExprListSyntax 的下标是 SyntaxChildrenIndex（非 Int）——转 Array 再取
        let children = Array(node.elements)
        guard children.count == 3,
              children[1].as(AssignmentExprSyntax.self) != nil,
              let lhs = children[0].as(MemberAccessExprSyntax.self) else {
            return .visitChildren
        }

        let property = lhs.declName.baseName.text
        guard property == "accessibilityIdentifier"
                || property == "accessibilityLabel" else { return .visitChildren }

        let location = node.startLocation(
            converter: SourceLocationConverter(
                fileName: filePath, tree: node.root))
        let source = SourceLocation(
            file: (filePath as NSString).lastPathComponent,
            line: location.line)

        // receiver 文本（`self.usernameField` / `_btn` / `[self view]`）
        let receiver = describe(expr: lhs.base?.trimmedDescription)
            ?? "<unknown-receiver>"
        let rhs = children[2]

        if property == "accessibilityLabel" {
            // label：字面量记下等 identifier 合并；非字面量忽略（无歧义）
            if let literal = rhs.as(StringLiteralExprSyntax.self),
               let value = literal.representedLiteralValue {
                labelsByReceiver[receiver] = value
                if let idx = byReceiver[receiver] {
                    elements[idx].label = value  // 后到先得（同 receiver 后写覆盖）
                }
            }
            return .visitChildren
        }

        // accessibilityIdentifier：主体条目
        if let literal = rhs.as(StringLiteralExprSyntax.self) {
            let value = literal.representedLiteralValue
            append(receiver: receiver, resolution: .literal, id: value,
                   source: source, type: typeHint(for: receiver))
        } else if let resolved = resolveConstant(from: rhs) {
            append(receiver: receiver, resolution: .constant, id: resolved,
                   source: source, type: typeHint(for: receiver))
        } else {
            // 认不出：dynamic/unknown 不猜（12.2）
            append(receiver: receiver, resolution: .dynamic, id: nil,
                   source: source, type: typeHint(for: receiver))
        }
        return .visitChildren
    }

    /// receiver 文本里的类型线索：`button` / `label` / `field` / `textField`
    /// 命名 → 映射（弱推断；无线索 → unknown）。**只做命名启发，不查类型。**
    private func typeHint(for receiver: String) -> String {
        let lower = receiver.lowercased()
        if lower.contains("button") || lower.contains("btn") { return "button" }
        if lower.contains("textfield") || lower.contains("field") { return "textfield" }
        if lower.contains("label") { return "text" }
        if lower.contains("cell") { return "cell" }
        if lower.contains("image") || lower.contains("imageview") { return "image" }
        return "unknown"
    }

    /// receiver 文本（`self.usernameField` / `_btn` / `[self view]`）：SwiftSyntax
    /// 不做类型解析，这里只用**语法文本**做 key——足够做「同 receiver 合并」
    /// 与命名启发，不宣称解析出了变量。
    private func describe(expr: String?) -> String? {
        guard let text = expr?.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty else { return nil }
        // self.x → x；裸名保留（_btn / btn 都有命名线索）
        if text.hasPrefix("self.") {
            return String(text.dropFirst("self.".count))
        }
        return text
    }

    private func resolveConstant(from expr: ExprSyntax) -> String? {
        if let member = expr.as(MemberAccessExprSyntax.self) {
            return constants.value(for: member.declName.baseName.text)
        }
        if let decl = expr.as(DeclReferenceExprSyntax.self) {
            return constants.value(for: decl.baseName.text)
        }
        return nil
    }

    private func append(receiver: String, resolution: ResolutionType,
                        id: String?, source: SourceLocation, type: String) {
        let element = ScannedElement(
            id: id ?? "UNKNOWN:\(source.file):\(source.line)",
            type: type,
            // accessibilityId 只在**解析出真实值**时非空；id 的 UNKNOWN
            // 前缀只是排障用占位（P2-7/R2-6），不是可定位的 id
            accessibilityId: id,
            resolutionType: resolution,
            containerType: containerStack.last,
            // 同 receiver 的 label 可能在本条 identifier 之前/之后出现：
            // 都合并到这条上（UIKit 的 label ↔ identifier 是一对）
            label: labelsByReceiver[receiver],
            source: source)
        if let idx = byReceiver[receiver] {
            // 同 receiver 重复赋值 identifier：后写覆盖（UIKit 常见 reset 模式）
            elements[idx] = element
            return
        }
        byReceiver[receiver] = elements.count
        elements.append(element)
    }

    func buildResult() -> ScanResult {
        let grouped = Dictionary(grouping: elements) { $0.containerType ?? "" }
        var screens: [ScannedScreen] = []
        for key in grouped.keys.sorted() where key != "" {
            screens.append(ScannedScreen(name: key, elements: grouped[key] ?? []))
        }
        return ScanResult(screens: screens.map(\.name), screenElements: screens)
    }
}
