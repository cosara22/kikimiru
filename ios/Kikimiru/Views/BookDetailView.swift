import SwiftUI

/// ブック詳細。再生ボタンはG1(再生核心)でプレイヤーに差し替える
struct BookDetailView: View {
    @EnvironmentObject var state: AppState
    let book: Book

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                CoverImage(book: book)
                    .frame(width: 200, height: 200)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                Text(book.title)
                    .font(.title3.bold())
                    .multilineTextAlignment(.center)
                if let authors = book.authors, !authors.isEmpty {
                    Text(authors.joined(separator: " / "))
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 16) {
                    if let slides = book.slides {
                        Label("スライド \(slides)枚", systemImage: "rectangle.on.rectangle")
                    }
                    if let d = book.duration {
                        Label(formatDuration(d), systemImage: "clock")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                Button {
                    state.playingBook = book
                } label: {
                    Label("再生", systemImage: "play.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)

                if let desc = book.description, !desc.isEmpty {
                    Text(desc)
                        .font(.callout)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding()
        }
        .navigationTitle(book.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s >= 3600 {
            return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
        }
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
