import AVFoundation
import Foundation
import MediaPlayer
import UIKit

/// 再生の中枢(アプリで1つ)。AVPlayerでのストリーミング再生と、
/// ロック画面・コントロールセンター統合(MPNowPlayingInfoCenter /
/// MPRemoteCommandCenter)、割り込み処理を担う。
/// ビューを閉じても再生は継続する(バックグラウンド再生の主体)。
@MainActor
final class PlayerEngine: ObservableObject {
    static let shared = PlayerEngine()

    @Published private(set) var book: Book?
    @Published private(set) var deck: Deck?
    @Published private(set) var content: ContentDoc?
    @Published private(set) var isPlaying = false
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var currentCue = 0
    @Published private(set) var rate: Double = 1.0

    private var player: AVPlayer?
    private var api: APIClient?
    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?
    private var commandsConfigured = false
    private var lastProgressSaveAt = Date.distantPast

    private init() {
        rate = UserDefaults.standard.object(forKey: "kikimiru.rate") as? Double ?? 1.0
        observeInterruptions()
    }

    var cues: [Deck.Cue] { deck?.cues ?? [] }

    // ---- 読み込み ----

    func load(book: Book, api: APIClient) async throws {
        // 同じブックなら読み込み直さない(ビューを開き直しても再生を継続)
        if self.book?.id == book.id, player != nil { return }
        // 別のブックへ切り替える前に、いまの位置を保存してアップする
        saveProgress(push: true)

        let lib = book.library ?? ""
        guard let deckURL = api.fileURL(library: lib, bookID: book.id, file: "deck.json") else {
            throw APIError.invalidURL
        }
        let (data, resp) = try await URLSession.shared.data(from: deckURL)
        if let http = resp as? HTTPURLResponse {
            if http.statusCode == 401 { throw APIError.authRequired }
            guard (200..<300).contains(http.statusCode) else { throw APIError.http(http.statusCode) }
        }
        let deck = try JSONDecoder().decode(Deck.self, from: data)

        // content.json(本文)は任意ファイル。無くても再生は成立する
        var contentDoc: ContentDoc?
        if let cURL = api.fileURL(library: lib, bookID: book.id, file: "content.json"),
           let (cData, cResp) = try? await URLSession.shared.data(from: cURL),
           (cResp as? HTTPURLResponse)?.statusCode == 200 {
            contentDoc = try? JSONDecoder().decode(ContentDoc.self, from: cData)
        }

        guard let audioURL = api.fileURL(library: lib, bookID: book.id, file: deck.audio.src) else {
            throw APIError.invalidURL
        }
        unloadCurrent()

        // AVPlayerは既定でCookieを送らないため、セッションCookieを明示的に渡す
        let cookies = HTTPCookieStorage.shared.cookies(for: audioURL) ?? []
        let asset = AVURLAsset(url: audioURL, options: [AVURLAssetHTTPCookiesKey: cookies])
        let item = AVPlayerItem(asset: asset)
        let newPlayer = AVPlayer(playerItem: item)

        newPlayer.defaultRate = Float(rate)
        self.player = newPlayer
        self.api = api
        self.book = book
        self.deck = deck
        self.content = contentDoc
        self.currentTime = 0
        self.currentCue = 0
        self.duration = deck.audio.duration ?? 0
        if duration <= 0, let d = try? await asset.load(.duration), d.isNumeric {
            duration = d.seconds
        }

        timeObserver = newPlayer.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main
        ) { time in
            let t = time.seconds
            Task { @MainActor in PlayerEngine.shared.tick(t) }
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { _ in
            Task { @MainActor in PlayerEngine.shared.handleEnded() }
        }

        // 続きから: 保存済み進捗があれば冒頭・末尾ぎわを除いて復元する
        if let rec = ProgressSync.shared.record(library: lib, bookID: book.id),
           rec.t > 3, duration <= 0 || rec.t < duration - 3 {
            seek(to: rec.t)
        }

        configureRemoteCommands()
        updateNowPlayingStatic()
        Task { await loadArtwork(api: api) }
    }

    private func unloadCurrent() {
        if let timeObserver, let player {
            player.removeTimeObserver(timeObserver)
        }
        timeObserver = nil
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        endObserver = nil
        player?.pause()
        player = nil
        isPlaying = false
    }

    // ---- 再生操作 ----

    func play() {
        guard let player else { return }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
        } catch {
            // セッション構成に失敗しても再生自体は試みる
        }
        player.play()
        player.rate = Float(rate)
        isPlaying = true
        updateNowPlayingDynamic()
    }

    /// 再生速度(選択は端末に記憶し、次のブックにも引き継ぐ)
    func setRate(_ r: Double) {
        rate = r
        UserDefaults.standard.set(r, forKey: "kikimiru.rate")
        player?.defaultRate = Float(r)
        if isPlaying {
            player?.rate = Float(r)
        }
        updateNowPlayingDynamic()
    }

    func pause() {
        player?.pause()
        isPlaying = false
        updateNowPlayingDynamic()
        saveProgress(push: true)
    }

    /// バックグラウンド移行時などに現在位置を確定保存する
    func flushProgress() {
        saveProgress(push: true)
    }

    func toggle() {
        if isPlaying { pause() } else { play() }
    }

    func seek(to t: Double) {
        guard let player else { return }
        let upper = duration > 0 ? duration : t
        let clamped = max(0, min(t, upper))
        currentTime = clamped
        player.seek(to: CMTime(seconds: clamped, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
        updateNowPlayingDynamic()
    }

    func skip(_ delta: Double) {
        seek(to: currentTime + delta)
    }

    // ---- 章送り(cuesが章) ----

    private func cueIndex(at t: Double) -> Int {
        var idx = 0
        for (i, c) in cues.enumerated() where c.t <= t + 0.05 {
            idx = i
        }
        return idx
    }

    func nextChapter() {
        let i = cueIndex(at: currentTime)
        guard i + 1 < cues.count else { return }
        seek(to: cues[i + 1].t)
    }

    func prevChapter() {
        guard !cues.isEmpty else { return }
        let i = cueIndex(at: currentTime)
        // 章頭から2秒以内なら前の章へ、それ以降は章頭へ戻す(Web版と同じ流儀)
        if currentTime - cues[i].t < 2, i > 0 {
            seek(to: cues[i - 1].t)
        } else {
            seek(to: cues[i].t)
        }
    }

    // ---- 内部状態 ----

    private func tick(_ t: Double) {
        guard t.isFinite else { return }
        currentTime = t
        let i = cueIndex(at: t)
        if i != currentCue {
            currentCue = i
        }
        // 再生中は5秒ごとに位置を保存する(書き込み過多を避ける間引き。Web版と同じ)
        if isPlaying, Date().timeIntervalSince(lastProgressSaveAt) >= 5 {
            lastProgressSaveAt = Date()
            saveProgress(push: true)
        }
    }

    private func saveProgress(push: Bool) {
        guard let book, currentTime > 0 else { return }
        ProgressSync.shared.save(
            library: book.library ?? "", bookID: book.id,
            t: currentTime, d: duration,
            cue: currentCue, total: cues.count)
        if push, let api {
            ProgressSync.shared.pushDirtyLater(api: api)
        }
    }

    private func handleEnded() {
        isPlaying = false
        currentTime = duration
        updateNowPlayingDynamic()
        saveProgress(push: true)
    }

    // ---- 割り込み(着信・イヤホン抜き) ----

    private func observeInterruptions() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { note in
            let info = note.userInfo
            Task { @MainActor in PlayerEngine.shared.handleInterruption(info) }
        }
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { note in
            let info = note.userInfo
            Task { @MainActor in PlayerEngine.shared.handleRouteChange(info) }
        }
    }

    private func handleInterruption(_ info: [AnyHashable: Any]?) {
        guard let raw = info?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        switch type {
        case .began:
            pause()
        case .ended:
            let optRaw = info?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            if AVAudioSession.InterruptionOptions(rawValue: optRaw).contains(.shouldResume) {
                play()
            }
        @unknown default:
            break
        }
    }

    private func handleRouteChange(_ info: [AnyHashable: Any]?) {
        guard let raw = info?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
        // イヤホンが抜けたら一時停止する(スピーカーで鳴り出さないように)
        if reason == .oldDeviceUnavailable {
            pause()
        }
    }

    // ---- ロック画面・コントロールセンター ----

    private func configureRemoteCommands() {
        guard !commandsConfigured else { return }
        commandsConfigured = true
        let c = MPRemoteCommandCenter.shared()
        c.playCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.play() }
            return .success
        }
        c.pauseCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.pause() }
            return .success
        }
        c.togglePlayPauseCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.toggle() }
            return .success
        }
        c.skipForwardCommand.preferredIntervals = [30]
        c.skipForwardCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.skip(30) }
            return .success
        }
        c.skipBackwardCommand.preferredIntervals = [30]
        c.skipBackwardCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.skip(-30) }
            return .success
        }
        c.nextTrackCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.nextChapter() }
            return .success
        }
        c.previousTrackCommand.addTarget { _ in
            Task { @MainActor in PlayerEngine.shared.prevChapter() }
            return .success
        }
        c.changePlaybackPositionCommand.addTarget { event in
            guard let e = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            let t = e.positionTime
            Task { @MainActor in PlayerEngine.shared.seek(to: t) }
            return .success
        }
    }

    private func updateNowPlayingStatic() {
        var info = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        info[MPMediaItemPropertyTitle] = book?.title ?? ""
        info[MPMediaItemPropertyArtist] = book?.authors?.joined(separator: " / ") ?? ""
        info[MPMediaItemPropertyPlaybackDuration] = duration
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        updateNowPlayingDynamic()
    }

    private func updateNowPlayingDynamic() {
        var info = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = currentTime
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? rate : 0.0
        info[MPMediaItemPropertyPlaybackDuration] = duration
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func loadArtwork(api: APIClient) async {
        guard let book, let cover = book.cover,
              let u = api.fileURL(library: book.library ?? "", bookID: book.id, file: cover) else {
            return
        }
        guard let (data, _) = try? await URLSession.shared.data(from: u),
              let img = UIImage(data: data) else { return }
        let art = MPMediaItemArtwork(boundsSize: img.size) { _ in img }
        var info = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        info[MPMediaItemPropertyArtwork] = art
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
}
