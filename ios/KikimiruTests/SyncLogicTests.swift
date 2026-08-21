import XCTest
@testable import Kikimiru

final class SyncLogicTests: XCTestCase {

    private func cues(_ ts: [Double]) -> [Deck.Cue] {
        ts.map { Deck.Cue(t: $0, slide: nil) }
    }

    // ---- cueIndex(章・スライドの現在位置解決) ----

    func testCueIndexAtStart() {
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 0), 0)
    }

    func testCueIndexBetweenCues() {
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 7.9), 0)
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 8.1), 1)
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 12), 1)
    }

    func testCueIndexBoundaryTolerance() {
        // 境界の直前ゆらぎは+0.05秒まで次のcueに寄せる
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 7.96), 1)
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 7.94), 0)
    }

    func testCueIndexBeyondEnd() {
        XCTAssertEqual(SyncLogic.cueIndex(cues: cues([0, 8, 16]), at: 999), 2)
    }

    func testCueIndexEmpty() {
        XCTAssertEqual(SyncLogic.cueIndex(cues: [], at: 5), 0)
    }

    // ---- mergeProgress(Last-Write-Wins) ----

    private func rec(t: Double, at: Double) -> ProgressRecord {
        ProgressRecord(t: t, d: 100, at: at, s: nil, n: nil)
    }

    func testServerNewerWins() {
        let merged = SyncLogic.mergeProgress(
            local: ["a/x": rec(t: 5, at: 100)],
            localDirty: ["a/x"],
            server: ["a/x": rec(t: 20, at: 200)])
        XCTAssertEqual(merged.records["a/x"]?.t, 20)
        // サーバ採用したキーはdirtyから外れる(採用直後の逆流アップを防ぐ)
        XCTAssertFalse(merged.dirty.contains("a/x"))
    }

    func testLocalNewerStaysAndMarksDirty() {
        let merged = SyncLogic.mergeProgress(
            local: ["a/x": rec(t: 30, at: 300)],
            localDirty: [],
            server: ["a/x": rec(t: 20, at: 200)])
        XCTAssertEqual(merged.records["a/x"]?.t, 30)
        XCTAssertTrue(merged.dirty.contains("a/x"))
    }

    func testServerOnlyKeyIsAdoptedWithoutDirty() {
        let merged = SyncLogic.mergeProgress(
            local: [:], localDirty: [],
            server: ["a/y": rec(t: 8, at: 100)])
        XCTAssertEqual(merged.records["a/y"]?.t, 8)
        XCTAssertFalse(merged.dirty.contains("a/y"))
    }

    func testLocalOnlyKeyBecomesDirty() {
        let merged = SyncLogic.mergeProgress(
            local: ["a/z": rec(t: 3, at: 100)],
            localDirty: [],
            server: [:])
        XCTAssertTrue(merged.dirty.contains("a/z"))
    }

    func testEqualTimestampIsStable() {
        let merged = SyncLogic.mergeProgress(
            local: ["a/x": rec(t: 5, at: 100)],
            localDirty: [],
            server: ["a/x": rec(t: 7, at: 100)])
        // 同時刻はローカル保持・dirtyにもしない(無限アップの防止)
        XCTAssertEqual(merged.records["a/x"]?.t, 5)
        XCTAssertFalse(merged.dirty.contains("a/x"))
    }

    // ---- APIClient のURL構築 ----

    func testAPIClientURLBuilding() {
        let api = APIClient(baseURL: URL(string: "http://192.168.1.10:8484")!)
        XCTAssertEqual(
            api.fileURL(library: "demo", bookID: "b1", file: "slides/s1.png")?.absoluteString,
            "http://192.168.1.10:8484/books/demo/b1/slides/s1.png")
        XCTAssertEqual(
            api.fileURL(library: "demo", bookID: "b1", file: "deck.json")?.absoluteString,
            "http://192.168.1.10:8484/books/demo/b1/deck.json")
    }
}
