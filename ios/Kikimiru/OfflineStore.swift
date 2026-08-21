import Foundation

/// ブック単位のオフライン保存の置き場所と登録簿。
/// レイアウトはサーバと同じ相対構成: <base>/<ライブラリ>/<ブックID>/{audio.mp3, deck.json, ...}
/// 完了マーカー(book.json=書誌スナップショット)があるブックだけを「保存済み」と扱う。
/// ダウンロード済みコンテンツはiCloudバックアップから除外する(再取得可能な資産のため)
enum OfflineStore {
    static var baseDir: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
            .appendingPathComponent("books", isDirectory: true)
        if !FileManager.default.fileExists(atPath: dir.path) {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            var d = dir
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? d.setResourceValues(values)
        }
        return dir
    }

    static func bookDir(library: String, bookID: String) -> URL {
        baseDir.appendingPathComponent(library, isDirectory: true)
            .appendingPathComponent(bookID, isDirectory: true)
    }

    static func fileURL(library: String, bookID: String, file: String) -> URL {
        bookDir(library: library, bookID: bookID).appendingPathComponent(file)
    }

    private static func markerURL(library: String, bookID: String) -> URL {
        fileURL(library: library, bookID: bookID, file: "book.json")
    }

    static func isDownloaded(library: String, bookID: String) -> Bool {
        FileManager.default.fileExists(atPath: markerURL(library: library, bookID: bookID).path)
    }

    /// 全ファイル取得後に呼ぶ。書誌スナップショットを完了マーカーとして書く
    static func markComplete(book: Book) {
        guard let data = try? JSONEncoder().encode(book) else { return }
        try? data.write(to: markerURL(library: book.library ?? "", bookID: book.id))
    }

    /// 保存済みブックの一覧(オフライン時の書棚)
    static func listDownloaded() -> [Book] {
        let fm = FileManager.default
        var books: [Book] = []
        let libs = (try? fm.contentsOfDirectory(at: baseDir, includingPropertiesForKeys: nil)) ?? []
        for lib in libs {
            let ids = (try? fm.contentsOfDirectory(at: lib, includingPropertiesForKeys: nil)) ?? []
            for idDir in ids {
                let marker = idDir.appendingPathComponent("book.json")
                if let data = try? Data(contentsOf: marker),
                   let book = try? JSONDecoder().decode(Book.self, from: data) {
                    books.append(book)
                }
            }
        }
        return books.sorted { $0.title < $1.title }
    }

    static func delete(library: String, bookID: String) {
        try? FileManager.default.removeItem(at: bookDir(library: library, bookID: bookID))
    }

    /// ブックの占有バイト数
    static func bytes(library: String, bookID: String) -> Int64 {
        let dir = bookDir(library: library, bookID: bookID)
        guard let en = FileManager.default.enumerator(at: dir,
                                                      includingPropertiesForKeys: [.fileSizeKey]) else {
            return 0
        }
        var total: Int64 = 0
        for case let url as URL in en {
            let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
            total += Int64(size)
        }
        return total
    }

    static func formatBytes(_ b: Int64) -> String {
        if b >= 1_000_000 {
            return String(format: "%.1f MB", Double(b) / 1_000_000)
        }
        return String(format: "%d KB", Int(Double(b) / 1000))
    }
}
