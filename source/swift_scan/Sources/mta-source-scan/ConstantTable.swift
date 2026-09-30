/// ConstantTable.swift — `static let` 字符串常量表（12.2 `constant` 解析）
///
/// 两遍扫描的第一遍。设计 12.2 硬约束：SwiftSyntax 只做语法分析、不做
/// 类型检查也不做跨模块符号解析——因此常量表**只收「工程内唯一命名」的
/// 顶层 static let 字符串常量**：
///
///   1. 唯一：同名多处声明 → 解析歧义 → **不收表**（调用方只能拿到 dynamic/
///      unknown；人工在 overrides 补齐，不得猜值）；
///   2. 顶层：只收 `type Foo { static let x = "…" }` 与 `enum Foo { static let x }`
///      形态——嵌套/局部常量同样有作用域歧义；
///   3. 字符串：只收字符串字面量初值；插值/表达式初值不收。
///
/// 第二遍（AccessibilityIDVisitor）遇到非字面量引用 `Foo.bar` 时查此表：
/// 命中且唯一 → `constant` + 常量值；未命中 → `dynamic` / `unknown`。

import Foundation
import SwiftSyntax

/// 常量名 → 字面量值。构造后不可变（纯数据）。
public struct ConstantTable {
    private let values: [String: String]
    /// 声明了但因**不可静态求值**被剔除的名字（歧义 / 非字符串初值 / var）
    /// ——引用这些名字不是「完全认不出」（unknown），而是 dynamic（12.2）。
    public let rejectedDeclarations: Set<String>

    init(values: [String: String], rejectedDeclarations: Set<String>) {
        self.values = values
        self.rejectedDeclarations = rejectedDeclarations
    }

    /// 唯一命中才返回值；歧义/未声明 → nil（调用方按 dynamic/unknown 处理）
    func value(for name: String) -> String? {
        values[name]
    }

    /// 调试/测试用：表内所有常量名
    var declaredNames: Set<String> {
        Set(values.keys)
    }
}

/// 非字面量声明的哨兵（文件级私有，与真实常量值不可能碰撞——含 NUL 字符）
private let nonLiteralSentinel = "\u{0}__MTA_NON_LITERAL__"

/// 第一遍：收集顶层 `static let` 字符串常量，剔除歧义声明。
public final class ConstantTableBuilder: SyntaxVisitor {
    private var candidates: [String: [String]] = [:]  // name → [value]（>1 = 歧义）

    public init() {
        super.init(viewMode: .sourceAccurate)
    }

    public override func visit(_ node: VariableDeclSyntax) -> SyntaxVisitorContinueKind {
        // 只收 static/class 的顶层变量声明。let = 常量候选；var = 登记到
        // rejected（声明在但可变、无「唯一值」语义——引用它落 dynamic 而非
        // unknown：不是「完全认不出」）。
        let modifierNames: [TokenSyntax] = node.modifiers.map(\.name)
        guard modifierNames.contains(where: { $0.tokenKind == .keyword(.static) })
                || modifierNames.contains(where: { $0.tokenKind == .keyword(.class) }) else {
            return .visitChildren
        }
        let isLet = node.bindingSpecifier.tokenKind == .keyword(.let)

        for binding in node.bindings {
            guard let pattern = binding.pattern.as(IdentifierPatternSyntax.self) else {
                continue
            }
            let name = pattern.identifier.text
            guard isLet else {
                // var：登记 rejected（值运行时可变，无法静态求唯一值）
                candidates[name, default: []].append(nonLiteralSentinel)
                continue
            }
            guard let initializer = binding.initializer else {
                // 无初值的 static let（如依赖注入）——同样不可静态求值
                candidates[name, default: []].append(nonLiteralSentinel)
                continue
            }
            // 只收字符串字面量；插值/表达式初值不收（无「唯一值」）
            guard let literal = initializer.value.as(StringLiteralExprSyntax.self),
                  let value = literal.representedLiteralValue else {
                candidates[name, default: []].append(nonLiteralSentinel)
                continue
            }
            candidates[name, default: []].append(value)
        }
        return .visitChildren
    }

    /// 构建最终表：唯一且字面量的才收。歧义（含不同值同名 / 同值同名两次）与
    /// 非字面量声明均剔除——12.2：无法唯一解析不得猜。
    func build() -> ConstantTable {
        var values: [String: String] = [:]
        var rejected: Set<String> = []
        for (name, found) in candidates {
            if found.count == 1, let only = found.first,
               only != nonLiteralSentinel {
                values[name] = only
            } else {
                rejected.insert(name)
            }
        }
        return ConstantTable(values: values, rejectedDeclarations: rejected)
    }
}
