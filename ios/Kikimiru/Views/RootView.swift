import SwiftUI

struct RootView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Group {
            switch state.phase {
            case .setup: SetupView()
            case .login: LoginView()
            case .shelf: LibraryView()
            }
        }
        // プレイヤーはナビゲーション位置に依存せず最上位のシートで開く
        .sheet(item: $state.playingBook) { book in
            PlayerView(book: book)
        }
    }
}
