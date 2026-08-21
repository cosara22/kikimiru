import SwiftUI

/// G1の最小プレイヤー画面。スライド同期表示はG2で載せる
struct PlayerView: View {
    @EnvironmentObject var state: AppState
    @ObservedObject var engine = PlayerEngine.shared
    let book: Book
    @State private var errorText: String?
    @State private var scrubbing = false
    @State private var scrubValue: Double = 0

    var body: some View {
        VStack(spacing: 20) {
            Capsule()
                .fill(.secondary.opacity(0.4))
                .frame(width: 36, height: 5)
                .padding(.top, 8)
            CoverImage(book: book)
                .frame(width: 240, height: 240)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.top, 12)
            Text(book.title)
                .font(.headline)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .padding(.horizontal)
            if !engine.cues.isEmpty {
                Text("チャプター \(engine.currentCue + 1) / \(engine.cues.count)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 4) {
                Slider(
                    value: Binding(
                        get: { scrubbing ? scrubValue : engine.currentTime },
                        set: { scrubValue = $0 }
                    ),
                    in: 0...max(engine.duration, 1)
                ) { editing in
                    if editing {
                        scrubValue = engine.currentTime
                        scrubbing = true
                    } else {
                        scrubbing = false
                        engine.seek(to: scrubValue)
                    }
                }
                HStack {
                    Text(fmt(scrubbing ? scrubValue : engine.currentTime))
                    Spacer()
                    Text(fmt(engine.duration))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal)

            HStack(spacing: 26) {
                Button { engine.prevChapter() } label: {
                    Image(systemName: "backward.end.fill")
                }
                Button { engine.skip(-30) } label: {
                    Image(systemName: "gobackward.30")
                }
                Button { engine.toggle() } label: {
                    Image(systemName: engine.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                        .font(.system(size: 64))
                }
                Button { engine.skip(30) } label: {
                    Image(systemName: "goforward.30")
                }
                Button { engine.nextChapter() } label: {
                    Image(systemName: "forward.end.fill")
                }
            }
            .font(.title2)

            if let errorText {
                Text(errorText)
                    .foregroundStyle(.red)
                    .font(.caption)
                    .padding(.horizontal)
            }
            Spacer()
        }
        .task {
            guard let api = state.api else { return }
            do {
                try await PlayerEngine.shared.load(book: book, api: api)
                PlayerEngine.shared.play()
            } catch {
                errorText = error.localizedDescription
            }
        }
    }

    private func fmt(_ t: Double) -> String {
        guard t.isFinite, t >= 0 else { return "0:00" }
        let s = Int(t)
        if s >= 3600 {
            return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
        }
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
