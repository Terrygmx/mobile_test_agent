// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "mta-source-scan",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "mta-source-scan", targets: ["mta-source-scan"]),
    ],
    dependencies: [
        // SwiftSyntax 必须与工具链同代。本地工具链是 Xcode 27 / Swift 6.4，
        // 对应 swift-syntax main 分支（release tag 509/510 系列跟不上 beta
        // 工具链的 API 变更——probe 实测 main 分支构建通过）。
        // CI 若用不同 Xcode，改这里（或按 tag 化）。
        .package(
            url: "https://github.com/apple/swift-syntax.git",
            branch: "main"
        ),
    ],
    targets: [
        .executableTarget(
            name: "mta-source-scan",
            dependencies: [
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftParser", package: "swift-syntax"),
            ],
            path: "Sources/mta-source-scan"
        ),
        .testTarget(
            name: "mta-source-scanTests",
            dependencies: [
                "mta-source-scan",
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftParser", package: "swift-syntax"),
            ],
            path: "Tests/mta-source-scanTests"
        ),
    ]
)
