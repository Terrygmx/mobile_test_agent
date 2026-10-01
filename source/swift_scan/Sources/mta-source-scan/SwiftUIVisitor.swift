/// SwiftUIVisitor.swift — SwiftUI identifier/label 识别（设计 12.1）
///
/// 识别模式：
///   1. `.accessibilityIdentifier("…")` —— 字面量 → `literal`；引用常量
///      → 查 ConstantTable，唯一命中 → `constant`，否则 `dynamic/unknown`；
///   2. 自定义 ViewModifier 常见模式（12.1）：`.mtaID("x")` / `.mtaScreen("x")`
///      一类单 String 参数 modifier——与 accessibilityIdentifier 同解析规则；
///   3. 元素类型推断（12.3 `type` 字段旧 schema 一直输出 "unknown"）：由
///      临近的 View 构造器推断 button / textfield / securefield / text /
///      cell；`screen:` 前缀的 modifier（mtaScreen）不产出元素，只注册 Screen。
///
/// 容器归属（12.1 `container_type`）：以最近一层的 View / ViewController
/// 类型（struct/class/enum/extension 声明名为键）为归属——不猜「就近」，
/// 只看语法嵌套。

import Foundation
import SwiftSyntax
import SwiftParser

/// 自定义 modifier 的白名单（12.1「常见模式」）。与其让 visitor 猜任意
/// modifier，不如显式登记——本 App 的 mtaScreen/mtaID 就是工程约定。
/// 未登记的 modifier 走 dynamic/unknown，不会误判。
let customIDModifiers: Set<String> = ["mtaID", "mtaIdentifier"]
let customScreenModifiers: Set<String> = ["mtaScreen"]

/// SwiftUI 元素类型构造器 → 12.3 type 值
let elementTypeByConstructor: [String: String] = [
    "Button": "button",
    "TextField": "textfield",
    "SecureField": "securetextfield",
    "Text": "text",
    "Image": "image",
    "Toggle": "toggle",
    "NavigationLink": "cell",   // M1 实测：NavigationLink 暴露为可点击 cell 节点
    "List": "list",
    "NavigationStack": "list",
    "Form": "list",
    "ScrollView": "list",
]

public final class SwiftUIVisitor: SyntaxVisitor {
    private let filePath: String
    private let constants: ConstantTable
    private var elements: [ScannedElement] = []
    private var containerStack: [String] = []
    private(set) var screenNames: [String] = []

    public init(filePath: String, constants: ConstantTable) {
        self.filePath = filePath
        self.constants = constants
        super.init(viewMode: .sourceAccurate)
    }

    // MARK: - 容器归属（12.1：以所在 View/ViewController 类型为 container）

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

    public override func visit(_ node: EnumDeclSyntax) -> SyntaxVisitorContinueKind {
        containerStack.append(node.name.text)
        return .visitChildren
    }

    public override func visitPost(_ node: EnumDeclSyntax) {
        _ = containerStack.popLast()
    }

    // MARK: - 元素类型/label 上下文：View 构造器
    //
    // 注意（探针实锤）：SwiftSyntax 的 visit 顺序里 `.accessibilityIdentifier`
    // 的 visit **早于**它修饰的构造器（modifier 的 callee 先被 walk），所以
    // 「前序压栈、identifier 读栈顶」的方案拿不到宿主。正确做法是在
    // handleModifierCall 里沿 callee.base 直接取宿主表达式——见
    // hostElementType(of:)。

    public override func visitPost(_ node: FunctionCallExprSyntax) {
        handleModifierCall(node)
    }

    /// 尾闭包里的首个字符串字面量 → label（Button { Text("登录") }）
    private func trailingLabelLiteral(of node: FunctionCallExprSyntax) -> String? {
        guard let closure = node.trailingClosure else { return nil }
        let visitor = _FirstStringLiteralVisitor()
        visitor.walk(closure.statements)
        return visitor.literal
    }

    // MARK: - identifier / marker modifier

    private func handleModifierCall(_ node: FunctionCallExprSyntax) {
        let name: String?
        if let member = node.calledExpression.as(MemberAccessExprSyntax.self) {
            name = member.declName.baseName.text
        } else if let decl = node.calledExpression.as(DeclReferenceExprSyntax.self) {
            name = decl.baseName.text
        } else {
            name = nil
        }
        guard let fn = name, fn != "" else { return }

        // mtaScreen 是 Screen marker，不产出元素（P1-03 已由 mtaScreen 处理）
        if customScreenModifiers.contains(fn) {
            if let arg = node.arguments.first?.expression,
               let literal = arg.as(StringLiteralExprSyntax.self),
               let value = literal.representedLiteralValue,
               !screenNames.contains(value) {
                screenNames.append(value)
            }
            return
        }
        guard fn == "accessibilityIdentifier" || customIDModifiers.contains(fn) else {
            return
        }
        guard let arg = node.arguments.first?.expression else { return }

        // 宿主类型推断（12.3 type 字段）：modifier 的 callee 是 MemberAccess，
        // 其 base 就是**被修饰的表达式**——`Text("x").accessibilityIdentifier`
        // 里 base = `Text("x")` 调用。（探针实锤：SwiftSyntax 的 visit 顺序是
        // modifier 先于宿主构造器，栈式 pending 拿不到宿主——只能从语法位置
        // 直接取。）
        let hostType = hostElementType(of: node.calledExpression)
        // label：Button(action:) { Text("登录") } 的尾闭包字面量
        let hostLabel = hostLabelLiteral(of: node.calledExpression)

        // 行号取 **参数表达式** 的位置而非整个调用：modifier 链上
        // `node.startLocation` 指向链首（多行链全部行号相同，排障时无法定位）。
        let source = location(of: arg)

        switch arg.as(StringLiteralExprSyntax.self) {
        case .some(let literal):
            guard let value = literal.representedLiteralValue else {
                // 纯插值/含插值的字面量：12.2 dynamic。id 用**静态前缀** +
                // 位置（如 "cell_*:138"）——下游一致性 Gate 靠前缀与人工
                // overrides（cell_alpha/cell_beta）匹配，而不只是 UNKNOWN:line
                // （那种占位无法与人工登记对应，Gate 会误报 REMOVED）。
                append(resolution: .dynamic,
                       id: interpolatedIdPrefix(literal)
                           ?? "UNKNOWN:\(source.file):\(source.line)",
                       type: hostType, label: hostLabel, source: source,
                       accessibilityId: nil)
                return
            }
            append(resolution: .literal, id: value, type: hostType,
                   label: hostLabel, source: source, accessibilityId: value)
        case .none:
            // 非字面量：查常量表（12.2 constant 档）。member access 形态
            // `Foo.bar` / 裸标识符 `bar` 都可查——表内键收集时已去歧义。
            let (resolution, resolved) = resolveConstant(from: arg)
            if resolution == .constant, let resolved {
                append(resolution: .constant, id: resolved, type: hostType,
                       label: hostLabel, source: source,
                       accessibilityId: resolved)
            } else {
                // 引用在但不可静态求值 → dynamic；完全认不出 → unknown（不猜）。
                // id=nil：accessibility_id 只在**解析出真实值**时非空；
                // UNKNOWN 占位只落在 id 字段（排障用），不会是假可定位 id。
                append(resolution: resolution, id: nil, type: hostType,
                       label: hostLabel, source: source)
            }
        }
    }

    /// 源码位置（行号取参数表达式自身的位置——modifier 链上整个调用的
    /// startLocation 指向链首，多行链行号全部相同）
    private func location(of expr: some SyntaxProtocol) -> SourceLocation {
        let line = expr.startLocation(
            converter: SourceLocationConverter(
                fileName: filePath, tree: expr.root)).line
        return SourceLocation(
            file: (filePath as NSString).lastPathComponent, line: line)
    }

    /// 宿主类型：沿 modifier 链的 callee base 找到第一个可识别的 View 构造器。
    private func hostElementType(of callee: ExprSyntax) -> String {
        var current = callee
        while let member = current.as(MemberAccessExprSyntax.self) {
            if let call = member.base?.as(FunctionCallExprSyntax.self) {
                if let decl = call.calledExpression.as(DeclReferenceExprSyntax.self) {
                    let name = decl.baseName.text
                    if let mapped = elementTypeByConstructor[name] {
                        return mapped
                    }
                    if name.hasSuffix("Button") { return "button" }
                    if name.hasSuffix("Cell") { return "cell" }
                }
                // 这个调用不是 View 构造器，继续沿它的 callee 向前
                current = ExprSyntax(call.calledExpression)
                continue
            }
            current = member.base ?? ExprSyntax(member)
        }
        // 裸调用形态（少见）：identifier(x) 直接作为函数调用
        if let call = current.as(FunctionCallExprSyntax.self),
           let decl = call.calledExpression.as(DeclReferenceExprSyntax.self),
           let mapped = elementTypeByConstructor[decl.baseName.text] {
            return mapped
        }
        return "unknown"
    }

    /// label：Button(action:) { Text("登录") } 形态取尾闭包首个字符串字面量
    private func hostLabelLiteral(of callee: ExprSyntax) -> String? {
        guard let member = callee.as(MemberAccessExprSyntax.self),
              let call = member.base?.as(FunctionCallExprSyntax.self),
              let closure = call.trailingClosure else { return nil }
        let visitor = _FirstStringLiteralVisitor()
        visitor.walk(closure.statements)
        return visitor.literal
    }

    /// 常量引用解析：`Foo.bar` → 查 "bar"；裸 `bar` → 查 "bar"。
    /// 表只含唯一命名的 static let（构造时已去歧义），所以查表命中即唯一。
    ///
    /// 返回 (resolution, value)：
    ///   - 表命中 → (constant, value)（12.2 第二档）
    ///   - 名字在**被剔除的声明集合**里（歧义 / 非字符串初值 / var）→ (dynamic, nil)
    ///     ——引用在，但值不可静态求值（不是「完全认不出」）
    ///   - 否则 → (unknown, nil)（运行时变量等）
    private func resolveConstant(from expr: ExprSyntax)
        -> (ResolutionType, String?) {
        let name: String?
        if let member = expr.as(MemberAccessExprSyntax.self) {
            name = member.declName.baseName.text
        } else if let decl = expr.as(DeclReferenceExprSyntax.self) {
            name = decl.baseName.text
        } else {
            name = nil
        }
        guard let name else { return (.unknown, nil) }
        if let value = constants.value(for: name) {
            return (.constant, value)
        }
        if constants.rejectedDeclarations.contains(name) {
            return (.dynamic, nil)
        }
        return (.unknown, nil)
    }
    private func append(resolution: ResolutionType, id: String?,
                        type: String, label: String?, source: SourceLocation,
                        accessibilityId: String? = nil) {
        elements.append(ScannedElement(
            id: id ?? "UNKNOWN:\(source.file):\(source.line)",
            type: type,
            // accessibilityId 独立于 id：dynamic 的 id 可以是插值前缀
            // （"cell_"，Gate 前缀匹配用），但 accessibility_id 必须为 nil
            // （12.2：解析不出唯一值的元素没有 accessibility id）。
            accessibilityId: accessibilityId,
            resolutionType: resolution,
            containerType: containerStack.last,
            label: label,
            source: source))
    }

    // MARK: - 组装输出

    func buildResult() -> ScanResult {
        // 按 container 分组成 screens[].elements[]（12.3）；无容器的元素归
        // 顶层 screens 名集合但不进任何 screens[].elements（不可归属 ≠ 猜归属）
        let grouped = Dictionary(grouping: elements) { $0.containerType ?? "" }
        var screens: [ScannedScreen] = []
        for key in grouped.keys.sorted() {
            guard key != "" else { continue }
            screens.append(ScannedScreen(name: key,
                                         elements: grouped[key] ?? []))
        }
        // 顶层 screens = **仅 mtaScreen marker 显式声明名**。
        // 教训（review P3-6）：container struct 名不是 marker 名
        // （SpikeTabScreen vs .mtaScreen("SpikeTab")）——混进顶层 screens
        // 会让下游 export 导出永远匹配不到真机的 screen 条目，属猜值；
        // 同时污染 12.3 metadata 的语义（顶层 screens 应是 marker 层）。
        // container 名只用于元素归属（screenElements），不进 screens。
        return ScanResult(screens: screenNames, screenElements: screens)
    }

    /// 插值字面量的静态前缀（`"cell_\(item.key)"` → "cell_"）。只取第一段
    /// 插值前的纯文本；无前置文本 → nil（回退 UNKNOWN:file:line）。
    private func interpolatedIdPrefix(_ literal: StringLiteralExprSyntax) -> String? {
        // StringLiteralSegmentListSyntax 本身就是段序列（无 .segments 成员）
        for seg in literal.segments {
            if let s = seg.as(StringSegmentSyntax.self) {
                if !s.content.text.isEmpty { return s.content.text }
                continue
            }
            // 遇到第一个插值段就停（只取它前面的静态文本）
            break
        }
        return nil
    }
}

private final class _FirstStringLiteralVisitor: SyntaxVisitor {
    var literal: String?

    init() {
        super.init(viewMode: .sourceAccurate)
    }

    override func visit(_ node: StringLiteralExprSyntax) -> SyntaxVisitorContinueKind {
        if literal == nil {
            literal = node.representedLiteralValue
        }
        return .skipChildren
    }
}
