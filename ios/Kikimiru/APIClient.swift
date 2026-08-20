import Foundation

enum APIError: LocalizedError {
    case authRequired
    case http(Int)
    case invalidURL

    var errorDescription: String? {
        switch self {
        case .authRequired: return "ログインが必要です"
        case .http(let code): return "サーバエラー (\(code))"
        case .invalidURL: return "URLが不正です"
        }
    }
}

/// kikimiru サーバの薄いHTTPクライアント。
/// セッションはHttpOnlyのCookie(URLSession.sharedの共有Cookieストレージが
/// アプリ再起動をまたいで永続化する)。Cookie失効(401)は authRequired として投げ、
/// 呼び出し側がKeychainのパスワードで再ログインする。
struct APIClient {
    var baseURL: URL

    private func url(_ path: String, query: [String: String] = [:]) -> URL? {
        var comp = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        comp?.path = path
        if !query.isEmpty {
            comp?.queryItems = query.sorted { $0.key < $1.key }
                .map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        return comp?.url
    }

    private func check(_ resp: URLResponse) throws {
        guard let http = resp as? HTTPURLResponse else { throw APIError.http(0) }
        if http.statusCode == 401 { throw APIError.authRequired }
        guard (200..<300).contains(http.statusCode) else { throw APIError.http(http.statusCode) }
    }

    private func get<T: Decodable>(_ type: T.Type, _ path: String,
                                   query: [String: String] = [:]) async throws -> T {
        guard let u = url(path, query: query) else { throw APIError.invalidURL }
        let (data, resp) = try await URLSession.shared.data(from: u)
        try check(resp)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func send(_ method: String, _ path: String, body: Data?) async throws {
        guard let u = url(path) else { throw APIError.invalidURL }
        var req = URLRequest(url: u)
        req.httpMethod = method
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        let (_, resp) = try await URLSession.shared.data(for: req)
        try check(resp)
    }

    // ---- 認証 ----

    func login(password: String) async throws {
        let body = try JSONEncoder().encode(["password": password])
        do {
            try await send("POST", "/api/login", body: body)
        } catch APIError.http(403) {
            // 総当たり対策のバックオフ中も認証失敗として扱う
            throw APIError.authRequired
        }
    }

    func logout() async {
        try? await send("POST", "/api/logout", body: nil)
    }

    // ---- 書誌 ----

    func libraries() async throws -> [Library] {
        try await get(LibrariesResponse.self, "/api/libraries").libraries
    }

    func books(library: String, query q: String? = nil,
               sort: String = "added") async throws -> [Book] {
        var query = ["library": library, "sort": sort]
        if let q, !q.isEmpty { query["q"] = q }
        return try await get(BooksResponse.self, "/api/books", query: query).books
    }

    func book(id: String, library: String) async throws -> Book {
        try await get(BookResponse.self, "/api/books/\(id)",
                      query: ["library": library]).book
    }

    /// ブックフォルダ内ファイル(音声・カバー・deck.json・content.json)の絶対URL
    func fileURL(library: String, bookID: String, file: String) -> URL? {
        url("/books/\(library)/\(bookID)/\(file)")
    }

    // ---- 進捗同期 ----

    func progress() async throws -> [String: ProgressRecord] {
        try await get(ProgressResponse.self, "/api/progress").progress
    }

    func putProgress(_ records: [String: ProgressRecord]) async throws {
        let body = try JSONEncoder().encode(ProgressResponse(progress: records))
        try await send("PUT", "/api/progress", body: body)
    }
}
