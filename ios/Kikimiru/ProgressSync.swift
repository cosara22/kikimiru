import Foundation

/// 再生進捗のローカル保存とサーバ同期。
/// Web版と同じ規約: キーは "ライブラリ/ブックID"、atはミリ秒エポック、
/// マージは Last-Write-Wins(atが新しい方を採用)。オフライン時はローカルに
/// 溜め、次の同期機会にアップする。
@MainActor
final class ProgressSync {
    static let shared = ProgressSync()

    private let storeKey = "kikimiru.progress"
    private var records: [String: ProgressRecord] = [:]
    private var dirty: Set<String> = []

    private init() {
        if let data = UserDefaults.standard.data(forKey: storeKey),
           let recs = try? JSONDecoder().decode([String: ProgressRecord].self, from: data) {
            records = recs
        }
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(records) {
            UserDefaults.standard.set(data, forKey: storeKey)
        }
    }

    static func key(library: String, bookID: String) -> String {
        "\(library)/\(bookID)"
    }

    func record(library: String, bookID: String) -> ProgressRecord? {
        records[Self.key(library: library, bookID: bookID)]
    }

    /// 再生位置の保存(呼び出し側で5秒間引き)
    func save(library: String, bookID: String, t: Double, d: Double, cue: Int, total: Int) {
        let key = Self.key(library: library, bookID: bookID)
        records[key] = ProgressRecord(
            t: t, d: d,
            at: Date().timeIntervalSince1970 * 1000,
            s: cue + 1, n: total)
        dirty.insert(key)
        persist()
    }

    /// サーバ→ローカルのLWWマージ後、ローカルが新しい分をアップする
    func sync(api: APIClient) async {
        do {
            let server = try await api.progress()
            let merged = SyncLogic.mergeProgress(local: records, localDirty: dirty,
                                                 server: server)
            records = merged.records
            dirty = merged.dirty
            persist()
            try await pushDirty(api: api)
        } catch {
            // オフライン等では手元の進捗のまま続行する(次回同期で追いつく)
        }
    }

    /// 変更分だけを非同期でアップする(再生中の間引き保存から呼ぶ)
    func pushDirtyLater(api: APIClient) {
        Task { try? await pushDirty(api: api) }
    }

    private func pushDirty(api: APIClient) async throws {
        guard !dirty.isEmpty else { return }
        var payload: [String: ProgressRecord] = [:]
        for key in dirty {
            if let r = records[key] { payload[key] = r }
        }
        try await api.putProgress(payload)
        dirty.subtract(payload.keys)
    }
}
