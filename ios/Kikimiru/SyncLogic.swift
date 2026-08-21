import Foundation

/// 再生同期まわりの純ロジック(ユニットテスト対象)。
/// 状態を持つ層(PlayerEngine / ProgressSync)から計算だけを切り出す
enum SyncLogic {
    /// 時刻tに対応するcueの添字。cuesは昇順(スキーマ規約)で、
    /// 境界の直前ゆらぎを吸収するため+0.05秒の許容を持つ(Web版と同じ流儀)
    static func cueIndex(cues: [Deck.Cue], at t: Double) -> Int {
        var idx = 0
        for (i, c) in cues.enumerated() where c.t <= t + 0.05 {
            idx = i
        }
        return idx
    }

    /// サーバ進捗とローカル進捗のLast-Write-Winsマージ。
    /// 返り値: (マージ後のローカル, アップすべきdirtyキー)
    static func mergeProgress(
        local: [String: ProgressRecord],
        localDirty: Set<String>,
        server: [String: ProgressRecord]
    ) -> (records: [String: ProgressRecord], dirty: Set<String>) {
        var records = local
        var dirty = localDirty
        for (key, rec) in server {
            if let mine = records[key] {
                if rec.at > mine.at {
                    records[key] = rec
                    dirty.remove(key)
                } else if mine.at > rec.at {
                    dirty.insert(key)
                }
            } else {
                records[key] = rec
            }
        }
        // サーバ側に無いローカル分もアップ対象にする
        for key in records.keys where server[key] == nil {
            dirty.insert(key)
        }
        return (records, dirty)
    }
}
