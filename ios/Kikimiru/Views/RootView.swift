import SwiftUI

struct RootView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        switch state.phase {
        case .setup: SetupView()
        case .login: LoginView()
        case .shelf: LibraryView()
        }
    }
}
