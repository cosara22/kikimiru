import SwiftUI

/// スライド同期プレイヤー(G2)。上=スライド面、下=章一覧(自動追従・タップでシーク)
struct PlayerView: View {
    @EnvironmentObject var state: AppState
    @ObservedObject var engine = PlayerEngine.shared
    let book: Book
    @State private var errorText: String?
    @State private var scrubbing = false
    @State private var scrubValue: Double = 0

    var body: some View {
        VStack(spacing: 12) {
            Capsule()
                .fill(.secondary.opacity(0.4))
                .frame(width: 36, height: 5)
                .padding(.top, 8)

            SlideView(slideRef: currentSlideRef,
                      text: currentSlideText,
                      imageURL: currentSlideImageURL,
                      fallbackTitle: book.title)
                .aspectRatio(16.0 / 10.0, contentMode: .fit)
                .padding(.horizontal)

            Text(book.title)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .padding(.horizontal)

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
                    speedMenu
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
                        .font(.system(size: 56))
                }
                Button { engine.skip(30) } label: {
                    Image(systemName: "goforward.30")
                }
                Button { engine.nextChapter() } label: {
                    Image(systemName: "forward.end.fill")
                }
            }
            .font(.title3)

            if let errorText {
                Text(errorText)
                    .foregroundStyle(.red)
                    .font(.caption)
                    .padding(.horizontal)
            }

            chapterList
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

    // ---- 現在スライドの解決 ----

    private var currentSlideRef: Deck.SlideRef? {
        guard let deck = engine.deck,
              engine.currentCue < deck.cues.count,
              let sid = deck.cues[engine.currentCue].slide else { return nil }
        return deck.slides.first { $0.id == sid }
    }

    private var currentSlideText: ContentDoc.SlideText? {
        guard let sid = currentSlideRef?.id else { return nil }
        return engine.content?.slides[sid]
    }

    private var currentSlideImageURL: URL? {
        guard let image = currentSlideRef?.image, let api = state.api else { return nil }
        return api.fileURL(library: book.library ?? state.currentLibrary,
                           bookID: book.id, file: image)
    }

    // ---- 速度 ----

    private var speedMenu: some View {
        Menu {
            ForEach([0.75, 1.0, 1.25, 1.5, 2.0], id: \.self) { r in
                Button {
                    engine.setRate(r)
                } label: {
                    if abs(engine.rate - r) < 0.01 {
                        Label(rateLabel(r), systemImage: "checkmark")
                    } else {
                        Text(rateLabel(r))
                    }
                }
            }
        } label: {
            Text(rateLabel(engine.rate))
                .font(.caption.monospacedDigit().bold())
                .padding(.horizontal, 10)
                .padding(.vertical, 3)
                .background(Capsule().fill(Color(.systemGray5)))
        }
    }

    private func rateLabel(_ r: Double) -> String {
        String(format: "%.4g×", r)
    }

    // ---- 章一覧(自動追従・タップでシーク) ----

    private var chapterList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(engine.cues.enumerated()), id: \.offset) { i, cue in
                        Button {
                            engine.seek(to: cue.t)
                        } label: {
                            HStack(spacing: 10) {
                                Text("\(i + 1)")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                                    .frame(width: 22, alignment: .trailing)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(chapterTitle(i))
                                        .font(.subheadline)
                                        .lineLimit(1)
                                    Text(fmt(cue.t))
                                        .font(.caption2.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if i == engine.currentCue {
                                    Image(systemName: "waveform")
                                        .font(.caption)
                                        .foregroundStyle(Color.accentColor)
                                }
                            }
                            .padding(.vertical, 7)
                            .padding(.horizontal, 12)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(i == engine.currentCue
                                          ? Color.accentColor.opacity(0.12)
                                          : Color.clear)
                            )
                        }
                        .buttonStyle(.plain)
                        .id(i)
                    }
                }
                .padding(.horizontal, 8)
            }
            .onChange(of: engine.currentCue) {
                withAnimation {
                    proxy.scrollTo(engine.currentCue, anchor: .center)
                }
            }
        }
    }

    private func chapterTitle(_ i: Int) -> String {
        guard i < engine.cues.count else { return "" }
        if let sid = engine.cues[i].slide,
           let t = engine.content?.slides[sid]?.title, !t.isEmpty {
            return t
        }
        return "スライド \(i + 1)"
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

/// スライド1枚の描画。画像スライドは全面表示、テキストスライドは種別で装飾を変える。
/// 未知のkindはcontent相当にフォールバック(スキーマ規約と同じ)
struct SlideView: View {
    let slideRef: Deck.SlideRef?
    let text: ContentDoc.SlideText?
    let imageURL: URL?
    let fallbackTitle: String

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(.secondarySystemBackground))
            if let imageURL {
                AsyncImage(url: imageURL) { img in
                    img.resizable().aspectRatio(contentMode: .fit)
                } placeholder: {
                    if let alt = text?.alt, !alt.isEmpty {
                        Text(alt)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding()
                    } else {
                        ProgressView()
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 14))
            } else {
                textBody
            }
        }
    }

    @ViewBuilder
    private var textBody: some View {
        let kind = slideRef?.kind ?? "content"
        VStack(alignment: kind == "content" ? .leading : .center, spacing: 10) {
            if kind == "title" {
                Text(displayTitle)
                    .font(.title2.bold())
                    .multilineTextAlignment(.center)
            } else if kind == "section" {
                Text(displayTitle)
                    .font(.title3.bold())
                    .multilineTextAlignment(.center)
            } else {
                if kind == "question" {
                    Label("問い", systemImage: "questionmark.circle")
                        .font(.caption.bold())
                        .foregroundStyle(Color.accentColor)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if !displayTitle.isEmpty {
                    Text(displayTitle)
                        .font(.headline)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if let bullets = text?.bullets, !bullets.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(bullets.enumerated()), id: \.offset) { _, b in
                            HStack(alignment: .top, spacing: 6) {
                                Text("•")
                                Text(b)
                            }
                        }
                    }
                    .font(.subheadline)
                }
            }
        }
        .padding(16)
    }

    private var displayTitle: String {
        if let t = text?.title, !t.isEmpty { return t }
        if slideRef?.kind == "title" { return fallbackTitle }
        return ""
    }
}
