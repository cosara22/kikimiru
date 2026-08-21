import SwiftUI

/// ブック詳細(再生+オフライン保存の管理)
struct BookDetailView: View {
    @EnvironmentObject var state: AppState
    @ObservedObject var dl = DownloadManager.shared
    let book: Book
    @State private var confirmDelete = false
    @State private var savedRefresh = 0   // 削除・完了後に保存状態表示を更新するためのトリガ

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

                downloadSection
                    .id(savedRefresh)

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

    // ---- オフライン保存 ----

    private var dlKey: String { DownloadManager.key(book) }
    private var lib: String { book.library ?? state.currentLibrary }

    @ViewBuilder
    private var downloadSection: some View {
        let phase = dl.states[dlKey]
        if case .downloading(let frac) = phase {
            VStack(spacing: 6) {
                ProgressView(value: min(max(frac, 0), 1))
                Text("ダウンロード中… \(Int(frac * 100))%")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } else if case .queued = phase {
            Label("ダウンロード待機中…", systemImage: "clock")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else if case .failed(let msg) = phase {
            VStack(spacing: 6) {
                Text("保存に失敗: \(msg)")
                    .font(.caption)
                    .foregroundStyle(.red)
                Button("再試行") {
                    dl.clearState(library: lib, bookID: book.id)
                    startDownload()
                }
            }
        } else if OfflineStore.isDownloaded(library: lib, bookID: book.id) {
            HStack(spacing: 14) {
                Label("保存済み(\(OfflineStore.formatBytes(OfflineStore.bytes(library: lib, bookID: book.id))))",
                      systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .foregroundStyle(.green)
                Button("保存を削除", role: .destructive) {
                    confirmDelete = true
                }
                .font(.callout)
            }
            .confirmationDialog("オフライン保存を削除しますか?",
                                isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("削除する", role: .destructive) {
                    OfflineStore.delete(library: lib, bookID: book.id)
                    dl.clearState(library: lib, bookID: book.id)
                    savedRefresh += 1
                }
            }
        } else {
            Button {
                startDownload()
            } label: {
                Label("オフライン保存", systemImage: "arrow.down.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(state.offlineMode)
        }
    }

    private func startDownload() {
        guard let api = state.api else { return }
        dl.enqueue(book: book, api: api)
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s >= 3600 {
            return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
        }
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
