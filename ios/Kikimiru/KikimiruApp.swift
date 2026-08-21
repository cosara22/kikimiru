import SwiftUI

@main
struct KikimiruApp: App {
    @StateObject private var state = AppState()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .task { await state.bootstrap() }
                .onChange(of: scenePhase) {
                    // バックグラウンド移行時に再生位置を確定保存する
                    if scenePhase == .background {
                        PlayerEngine.shared.flushProgress()
                    }
                }
        }
    }
}
