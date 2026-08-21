import SwiftUI

/// 書棚(ブック一覧+検索)
struct LibraryView: View {
    @EnvironmentObject var state: AppState
    @State private var books: [Book] = []
    @State private var query = ""
    @State private var loading = false
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Group {
                if loading && books.isEmpty {
                    ProgressView()
                } else if let errorText {
                    VStack(spacing: 12) {
                        Text(errorText).foregroundStyle(.secondary)
                        Button("再読み込み") { Task { await reload() } }
                    }
                } else {
                    List(books) { book in
                        NavigationLink(value: book) {
                            BookRow(book: book)
                        }
                    }
                    .listStyle(.plain)
                    .navigationDestination(for: Book.self) { book in
                        BookDetailView(book: book)
                    }
                }
            }
            .navigationTitle(state.offlineMode ? "本棚(オフライン)" : "本棚")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("ログアウト", role: .destructive) {
                            Task { await state.logout() }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .searchable(text: $query, prompt: "タイトル・著者・タグを検索")
            .onChange(of: query) {
                Task { await reload() }
            }
            .task { await reload() }
            .refreshable { await reload() }
        }
    }

    private func reload() async {
        // オフライン起動時は保存済みブックだけを一覧する(検索はローカル絞り込み)
        if state.offlineMode {
            let saved = OfflineStore.listDownloaded()
            books = query.isEmpty ? saved : saved.filter {
                $0.title.localizedCaseInsensitiveContains(query)
            }
            errorText = nil
            return
        }
        guard let api = state.api, !state.currentLibrary.isEmpty else { return }
        loading = true
        defer { loading = false }
        do {
            books = try await api.books(library: state.currentLibrary, query: query)
            errorText = nil
        } catch APIError.authRequired {
            state.phase = .login
        } catch {
            errorText = error.localizedDescription
        }
    }
}

struct BookRow: View {
    let book: Book

    var body: some View {
        HStack(spacing: 12) {
            CoverImage(book: book)
                .frame(width: 52, height: 52)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            VStack(alignment: .leading, spacing: 3) {
                Text(book.title).lineLimit(2)
                if let authors = book.authors, !authors.isEmpty {
                    Text(authors.joined(separator: " / "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// カバー画像。保存済みならローカルファイル、それ以外はサーバから取得する
/// (セッションCookieはURLSessionの共有Cookieストレージから自動で付く)
struct CoverImage: View {
    @EnvironmentObject var state: AppState
    let book: Book

    private var coverURL: URL? {
        guard let cover = book.cover, !cover.isEmpty else { return nil }
        let lib = book.library ?? state.currentLibrary
        if OfflineStore.isDownloaded(library: lib, bookID: book.id) {
            let local = OfflineStore.fileURL(library: lib, bookID: book.id, file: cover)
            if FileManager.default.fileExists(atPath: local.path) {
                return local
            }
        }
        return state.api?.fileURL(library: lib, bookID: book.id, file: cover)
    }

    var body: some View {
        if let u = coverURL {
            AsyncImage(url: u) { img in
                img.resizable().aspectRatio(contentMode: .fill)
            } placeholder: {
                Color.gray.opacity(0.2)
            }
        } else {
            ZStack {
                Color.gray.opacity(0.2)
                Image(systemName: "book")
                    .foregroundStyle(.secondary)
            }
        }
    }
}
