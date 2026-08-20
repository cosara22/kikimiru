import Foundation
import Security

/// サーバパスワード1件だけを保存する最小のKeychainラッパー。
/// Cookie失効時(30日超の未使用など)の静かな再ログインに使う。
enum Keychain {
    private static let service = "app.kikimiru.ios"
    private static let account = "server-password"

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func savePassword(_ password: String) {
        SecItemDelete(baseQuery as CFDictionary)
        var attrs = baseQuery
        attrs[kSecValueData as String] = Data(password.utf8)
        SecItemAdd(attrs as CFDictionary, nil)
    }

    static func loadPassword() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func deletePassword() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}
