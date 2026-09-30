/// main.swift — mta-source-scan 入口（设计 12.1/12.2/12.3）
///
/// 用法：
///   mta-source-scan <file.swift|.m|.h> [more files...] [-o out.json]
///
/// 输出（12.3 schema）：
///   { "screens": [name], "screen_elements": [{"name": …, "elements": [...]}] }
/// 与旧 /tmp/swift_scan 单文件输出保持字段兼容（screens 顶层保留），
/// screen_elements 是新增的 12.3 结构化形状；元素级多出 container_type /
/// label（12.1/12.3）。
///
/// **两遍扫描**（12.2 constant 档的前提）：
///   第一遍 ConstantTableBuilder 收集**唯一命名**的 static let 字符串常量；
///   第二遍 SwiftUI/UIKit visitor 识别元素并查表。
/// SwiftSyntax 只做语法分析（12.2 硬约束）：无法唯一解析一律 dynamic/unknown，
/// 不得猜值。

import Foundation
import SwiftSyntax
import SwiftParser

struct CLIError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func parseArgs() throws -> (inputs: [String], output: String?) {
    var inputs: [String] = []
    var output: String?
    var args = Array(CommandLine.arguments.dropFirst())
    while let arg = args.first {
        args.removeFirst()
        switch arg {
        case "-o", "--output":
            guard let path = args.first else {
                throw CLIError("\(arg) requires a path")
            }
            args.removeFirst()
            output = path
        case "-h", "--help":
            print("usage: mta-source-scan <file.swift|.m|.h> [...] [-o out.json]")
            throw CLIError("help requested")
        default:
            if arg.hasPrefix("-") {
                throw CLIError("unknown option: \(arg)")
            }
            inputs.append(arg)
        }
    }
    guard !inputs.isEmpty else {
        throw CLIError("no input files")
    }
    return (inputs, output)
}

private func readSource(_ path: String) throws -> String {
    let url = URL(fileURLWithPath: path)
    guard FileManager.default.fileExists(atPath: url.path) else {
        throw CLIError("file not found: \(path)")
    }
    do {
        return try String(contentsOf: url, encoding: .utf8)
    } catch {
        throw CLIError("cannot read \(path) as UTF-8")
    }
}

private func language(of path: String) -> String {
    (path.hasSuffix(".m") || path.hasSuffix(".h")) ? "objc" : "swift"
}

// MARK: - ObjC 旁路（Phase 1 的 objc_scan.py，文字解码）

private struct ObjcScanOutput: Decodable {
    struct ObjElement: Decodable {
        let id: String
        let type: String
        let accessibilityId: String?
        let resolutionType: String
        let source: SourceLocation
        let screen: String?   // objc_scan.py 顶层 screen 名会落到元素里？不——
        // objc_scan.py 顶层输出 {screen, elements[]}，screen 是文件级。
        // 这里保留字段为 nil 兼容旧输出；归属由下方容器逻辑补齐。
    }
    let screen: String?
    let elements: [ObjElement]?
}

private func scanObjC(path: String) throws -> ScanResult {
    let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().path
    let script = root + "/objc_scan.py"
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    proc.arguments = [script, path]
    let pipe = Pipe()
    proc.standardOutput = pipe
    let err = Pipe()
    proc.standardError = err
    try proc.run()
    proc.waitUntilExit()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    guard !data.isEmpty else {
        let msg = String(data: err.fileHandleForReading.readDataToEndOfFile(),
                        encoding: .utf8) ?? "empty objc_scan output"
        throw CLIError("objc_scan failed on \(path): \(msg)")
    }
    let decoded = try JSONDecoder().decode(ObjcScanOutput.self, from: data)
    let container = decoded.screen ?? "ObjCSources"
    let elements = (decoded.elements ?? []).map { raw in
        ScannedElement(
            id: raw.id, type: raw.type, accessibilityId: raw.accessibilityId,
            resolutionType: ResolutionType(rawValue: raw.resolutionType) ?? .unknown,
            containerType: container, label: nil, source: raw.source)
    }
    return ScanResult(screens: [container],
                      screenElements: [ScannedScreen(name: container,
                                                     elements: elements)])
}

// MARK: - 主流程

do {
    let (inputs, output) = try parseArgs()

    // 第一遍：常量表（跨全部输入文件——constant 的 static let 可能在别的文件）
    let builder = ConstantTableBuilder()
    for path in inputs where language(of: path) == "swift" {
        builder.walk(Parser.parse(source: try readSource(path)))
    }
    let constants = builder.build()

    // 第二遍：逐文件识别。Swift 文件同时跑 SwiftUI + UIKit visitor（两个
    // visitor 覆盖的模式互斥，不会双记；都用同一 tree，成本可控）。
    var allScreens: Set<String> = []
    var merged: [String: [ScannedElement]] = [:]
    for path in inputs {
        let result: ScanResult
        if language(of: path) == "swift" {
            let tree = Parser.parse(source: try readSource(path))
            let swiftUI = SwiftUIVisitor(filePath: path, constants: constants)
            swiftUI.walk(tree)
            let uiKit = UIKitVisitor(filePath: path, constants: constants)
            uiKit.walk(tree)
            result = ScanResult(
                screens: Set(swiftUI.buildResult().screens)
                    .union(uiKit.buildResult().screens).sorted(),
                screenElements: swiftUI.buildResult().screenElements
                    + uiKit.buildResult().screenElements)
        } else {
            result = try scanObjC(path: path)
        }
        allScreens.formUnion(result.screens)
        for screen in result.screenElements {
            merged[screen.name, default: []].append(contentsOf: screen.elements)
        }
    }

    let scanResult = ScanResult(
        screens: allScreens.sorted(),
        screenElements: merged.keys.sorted().map {
            ScannedScreen(name: $0, elements: merged[$0] ?? [])
        })

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    let data = try encoder.encode(scanResult)
    if let output = output {
        try data.write(to: URL(fileURLWithPath: output))
    } else {
        print(String(data: data, encoding: .utf8)!)
    }
} catch let error as CLIError where error.description == "help requested" {
    exit(0)
} catch {
    FileHandle.standardError.write(
        "mta-source-scan: \(error)\n".data(using: .utf8)!)
    exit(2)
}
