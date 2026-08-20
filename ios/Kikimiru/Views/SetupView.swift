import SwiftUI

struct SetupView: View {
    @EnvironmentObject var state: AppState
    @State private var connecting = false

    var body: some View {
        NavigationStack {
            Form {
                Section("サーバURL") {
                    TextField("http://192.168.1.10:8000", text: $state.serverURLText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    Button {
                        state.saveServerURL()
                        connecting = true
                        Task {
                            await state.bootstrap()
                            connecting = false
                        }
                    } label: {
                        if connecting {
                            ProgressView()
                        } else {
                            Text("接続")
                        }
                    }
                    .disabled(state.serverURLText.isEmpty || connecting)
                }
                if let err = state.lastError {
                    Section {
                        Text(err).foregroundStyle(.red)
                    }
                }
                Section("メモ") {
                    Text("実機からLAN経由で接続する場合は、サーバ起動時に --allow-host <このURLのホスト名かIP> を付けてください(未指定だと403になります)。シミュレータからは http://127.0.0.1:ポート で接続できます。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("kikimiru")
        }
    }
}
