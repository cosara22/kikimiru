import Foundation

/// ブック一式(音声+deck+content+表紙+スライド画像)のダウンロード管理。
/// バックグラウンドURLSessionを使い、アプリが閉じてもOSがダウンロードを継続する。
/// ブック間は直列(Web版のキュー設計を踏襲)、1ブック内のファイルは同時に取得する。
final class DownloadManager: NSObject, ObservableObject, URLSessionDownloadDelegate {
    static let shared = DownloadManager()

    enum Phase {
        case queued
        case downloading(Double)
        case done
        case failed(String)
    }

    /// キーは "ライブラリ/ブックID"
    @Published private(set) var states: [String: Phase] = [:]
    var backgroundCompletionHandler: (() -> Void)?

    private lazy var session: URLSession = {
        let cfg = URLSessionConfiguration.background(withIdentifier: "app.kikimiru.dl")
        cfg.sessionSendsLaunchEvents = true
        return URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }()

    // 以下の可変状態はMainActorからのみ触る
    private var pendingFiles: [String: Int] = [:]
    private var bookMeta: [String: Book] = [:]
    private var queue: [(Book, URL)] = []   // (ブック, サーバbaseURL)
    private var activeKey: String?

    static func key(_ book: Book) -> String {
        "\(book.library ?? "")/\(book.id)"
    }

    // ---- キュー投入(ブック間は直列) ----

    @MainActor
    func enqueue(book: Book, api: APIClient) {
        let key = Self.key(book)
        if case .downloading = states[key] { return }
        if case .queued = states[key] { return }
        states[key] = .queued
        queue.append((book, api.baseURL))
        pumpQueue()
    }

    @MainActor
    private func pumpQueue() {
        guard activeKey == nil, !queue.isEmpty else { return }
        let (book, baseURL) = queue.removeFirst()
        let key = Self.key(book)
        activeKey = key
        Task { await start(book: book, api: APIClient(baseURL: baseURL)) }
    }

    @MainActor
    private func start(book: Book, api: APIClient) async {
        let lib = book.library ?? ""
        let key = Self.key(book)
        do {
            // deck.json を先に取得して必要ファイル一覧を確定する
            guard let deckURL = api.fileURL(library: lib, bookID: book.id, file: "deck.json") else {
                throw APIError.invalidURL
            }
            let (data, resp) = try await URLSession.shared.data(from: deckURL)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                throw APIError.http((resp as? HTTPURLResponse)?.statusCode ?? 0)
            }
            let deck = try JSONDecoder().decode(Deck.self, from: data)
            let dir = OfflineStore.bookDir(library: lib, bookID: book.id)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try data.write(to: dir.appendingPathComponent("deck.json"))

            // (相対パス, 任意ファイルか)。任意ファイルは404でも失敗扱いにしない
            var files: [(String, Bool)] = [(deck.audio.src, false), ("content.json", true)]
            if let cover = book.cover, !cover.isEmpty {
                files.append((cover, true))
            }
            for s in deck.slides {
                if let img = s.image, !img.isEmpty {
                    files.append((img, true))
                }
            }
            bookMeta[key] = book
            pendingFiles[key] = files.count
            states[key] = .downloading(0)
            for (rel, optional) in files {
                guard let u = api.fileURL(library: lib, bookID: book.id, file: rel) else { continue }
                let task = session.downloadTask(with: u)
                task.taskDescription = "\(key)|\(rel)|\(optional ? "opt" : "req")"
                task.resume()
            }
        } catch {
            states[key] = .failed(error.localizedDescription)
            activeKey = nil
            pumpQueue()
        }
    }

    @MainActor
    func clearState(library: String, bookID: String) {
        states["\(library)/\(bookID)"] = nil
    }

    @MainActor
    private func fileCompleted(key: String, error: String?) {
        if let error {
            if pendingFiles[key] != nil {
                states[key] = .failed(error)
                pendingFiles[key] = nil
                activeKey = nil
                pumpQueue()
            }
            return
        }
        guard var left = pendingFiles[key] else { return }
        left -= 1
        pendingFiles[key] = left
        if left <= 0 {
            pendingFiles[key] = nil
            if let book = bookMeta[key] {
                OfflineStore.markComplete(book: book)
            }
            states[key] = .done
            activeKey = nil
            pumpQueue()
        }
    }

    // ---- URLSessionDownloadDelegate(セッションのキューから呼ばれる) ----

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        guard let desc = downloadTask.taskDescription else { return }
        let parts = desc.split(separator: "|", maxSplits: 2).map(String.init)
        guard parts.count == 3 else { return }
        let key = parts[0]
        let rel = parts[1]
        let optional = parts[2] == "opt"
        let comps = key.split(separator: "/", maxSplits: 1).map(String.init)
        guard comps.count == 2 else { return }

        // 一時ファイルはこのメソッドを抜けると消えるため、移動は同期的にここで行う
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        var fileError: String?
        if status == 200 {
            let dest = OfflineStore.fileURL(library: comps[0], bookID: comps[1], file: rel)
            do {
                try FileManager.default.createDirectory(
                    at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
                try? FileManager.default.removeItem(at: dest)
                try FileManager.default.moveItem(at: location, to: dest)
            } catch {
                fileError = error.localizedDescription
            }
        } else if !optional {
            fileError = "HTTP \(status) (\(rel))"
        }
        let err = fileError
        Task { @MainActor in
            self.fileCompleted(key: key, error: err)
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        // 音声がバイト数の大半を占めるため、最大ファイルの進捗で近似する
        guard totalBytesExpectedToWrite > 0, let desc = downloadTask.taskDescription,
              let keySub = desc.split(separator: "|").first else { return }
        let key = String(keySub)
        let frac = Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)
        Task { @MainActor in
            if case .downloading(let cur) = self.states[key], frac > cur {
                self.states[key] = .downloading(frac)
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard let error, let desc = task.taskDescription,
              let keySub = desc.split(separator: "|").first else { return }
        let key = String(keySub)
        let msg = error.localizedDescription
        Task { @MainActor in
            self.fileCompleted(key: key, error: msg)
        }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async {
            self.backgroundCompletionHandler?()
            self.backgroundCompletionHandler = nil
        }
    }
}
