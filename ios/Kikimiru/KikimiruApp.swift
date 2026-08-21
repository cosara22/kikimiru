import SwiftUI
import UIKit

/// バックグラウンドDLの完了イベント受け口(アプリがOSに再起動されて呼ばれる)
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        DownloadManager.shared.backgroundCompletionHandler = completionHandler
    }
}

@main
struct KikimiruApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
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
