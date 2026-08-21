import Foundation
import SwiftUI

/// 画面の大状態: サーバ設定 → ログイン → 書棚
enum AppPhase {
    case setup
    case login
    case shelf
}

@MainActor
final class AppState: ObservableObject {
    @Published var phase: AppPhase = .setup
    @Published var serverURLText: String
    @Published var libraries: [Library] = []
    @Published var currentLibrary: String = ""
    @Published var lastError: String?

    init() {
        serverURLText = UserDefaults.standard.string(forKey: "kikimiru.serverURL") ?? ""
    }

    var api: APIClient? {
        guard let u = URL(string: serverURLText), u.scheme != nil, u.host != nil else {
            return nil
        }
        return APIClient(baseURL: u)
    }

    /// 起動・接続時: 疎通確認 →(Cookie生存 or Keychainで静かに再ログイン)→ 書棚へ。
    /// 認証が立て直せなければログイン画面、疎通自体が失敗ならサーバ設定画面に落とす
    func bootstrap() async {
        guard !serverURLText.isEmpty else { phase = .setup; return }
        guard let api else {
            lastError = "サーバURLが不正です(例: http://192.168.1.10:8000)"
            phase = .setup
            return
        }
        do {
            try await loadLibraries(api)
            lastError = nil
            phase = .shelf
        } catch APIError.authRequired {
            if let pw = Keychain.loadPassword(),
               (try? await api.login(password: pw)) != nil,
               (try? await loadLibraries(api)) != nil {
                lastError = nil
                phase = .shelf
                return
            }
            #if DEBUG
            // SSH越しの自動受け入れ検証用(Debugビルド限定):
            // simctl launch の環境変数からパスワードを受けて自動ログインする
            if let pw = ProcessInfo.processInfo.environment["KIKIMIRU_DEBUG_PASSWORD"],
               (try? await api.login(password: pw)) != nil,
               (try? await loadLibraries(api)) != nil {
                Keychain.savePassword(pw)
                lastError = nil
                phase = .shelf
                return
            }
            #endif
            lastError = nil
            phase = .login
        } catch {
            lastError = error.localizedDescription
            phase = .setup
        }
    }

    func saveServerURL() {
        UserDefaults.standard.set(serverURLText, forKey: "kikimiru.serverURL")
    }

    func login(password: String) async {
        guard let api else {
            lastError = "サーバURLが不正です"
            return
        }
        do {
            try await api.login(password: password)
            Keychain.savePassword(password)
            try await loadLibraries(api)
            lastError = nil
            phase = .shelf
        } catch APIError.authRequired {
            lastError = "パスワードが違います"
        } catch {
            lastError = error.localizedDescription
        }
    }

    func logout() async {
        if let api { await api.logout() }
        Keychain.deletePassword()
        phase = .login
    }

    private func loadLibraries(_ api: APIClient) async throws {
        let libs = try await api.libraries()
        libraries = libs
        let saved = UserDefaults.standard.string(forKey: "kikimiru.library")
        if let saved, libs.contains(where: { $0.id == saved }) {
            currentLibrary = saved
        } else {
            currentLibrary = libs.first?.id ?? ""
        }
        UserDefaults.standard.set(currentLibrary, forKey: "kikimiru.library")
    }
}
