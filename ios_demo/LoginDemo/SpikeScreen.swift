import SwiftUI

/// P1-03 Spike（设计 13.3）：验证 `mtaScreen()`（accessibilityElement(children: .contain)
/// + accessibilityIdentifier("screen.<Name>")）在三种容器场景下：
///   1. marker 唯一可见；
///   2. 子元素 accessibilityIdentifier 不被容器折叠/吞掉。
/// 三场景：TabView / NavigationStack push / Sheet。通过启动参数 -UITestSpikeScreen 激活，
/// 不影响正常用例路径。
/// mtaScreen() 定义已迁至 MTAScreen.swift（Task 1.5 五屏共用，避免重复声明）。

// MARK: - 场景 1：TabView

struct SpikeTabScreen: View {
    var body: some View {
        TabView {
            VStack(spacing: 12) {
                Text("Tab inner title")
                    .accessibilityIdentifier("tab_inner_title")
                Button("Tab button") {}
                    .accessibilityIdentifier("tab_inner_button")
            }
            .mtaScreen("SpikeTab")
            .tabItem { Label("Spike", systemImage: "square.grid.2x2") }
        }
    }
}

// MARK: - 场景 2：NavigationStack push

struct SpikeNavRoot: View {
    var body: some View {
        NavigationStack {
            VStack {
                Text("Nav root")
                    .accessibilityIdentifier("nav_root_title")
                NavigationLink("Go detail", value: "detail")
            }
            .mtaScreen("SpikeNavRoot")
            .navigationTitle("SpikeNav")
        }
    }
}

struct SpikeNavDetail: View {
    var body: some View {
        Text("Nav detail content")
            .accessibilityIdentifier("nav_detail_text")
            .mtaScreen("SpikeNavDetail")
            .navigationTitle("Detail")
    }
}

// MARK: - 场景 3：Sheet

struct SpikeSheetHost: View {
    @State private var showSheet = false

    var body: some View {
        VStack {
            Button("Open sheet") { showSheet = true }
                .accessibilityIdentifier("sheet_open_button")
        }
        .mtaScreen("SpikeSheetHost")
        .sheet(isPresented: $showSheet) {
            VStack {
                Text("Sheet content")
                    .accessibilityIdentifier("sheet_inner_text")
            }
            .mtaScreen("SpikeSheet")
            .presentationDetents([.medium])
        }
    }
}

/// Spike 入口：三个场景纵向排布（sheet 需手动打开，脚本分两步验证）
struct SpikeScreenRoot: View {
    var body: some View {
        TabView {
            SpikeTabScreen()
                .tabItem { Label("Tab", systemImage: "1.square") }
                .accessibilityIdentifier("spike_tab_tab")
            NavigationStack { SpikeNavDetail() }
                .tabItem { Label("Nav", systemImage: "2.square") }
                .accessibilityIdentifier("spike_tab_nav")
            SpikeSheetHost()
                .tabItem { Label("Sheet", systemImage: "3.square") }
                .accessibilityIdentifier("spike_tab_sheet")
        }
    }
}
