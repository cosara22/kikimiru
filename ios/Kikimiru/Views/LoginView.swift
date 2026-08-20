import SwiftUI

struct LoginView: View {
    @EnvironmentObject var state: AppState
    @State private var password = ""
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Section("パスワード") {
                    SecureField("パスワード", text: $password)
                        .textInputAutocapitalization(.never)
                }
                Section {
                    Button {
                        busy = true
                        Task {
                            await state.login(password: password)
                            busy = false
                        }
                    } label: {
                        if busy {
                            ProgressView()
                        } else {
                            Text("ログイン")
                        }
                    }
                    .disabled(password.isEmpty || busy)
                    Button("サーバ設定へ戻る") {
                        state.phase = .setup
                    }
                }
                if let err = state.lastError {
                    Section {
                        Text(err).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("ログイン")
        }
    }
}
