import Foundation

// サーバAPIの応答モデル。書誌フィールドはスキーマv2ですべて任意のため、
// id/title 以外はオプショナルで受けて欠損に耐える

/// GET /api/libraries の1件
struct Library: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let books: Int?
}

struct LibrariesResponse: Codable {
    let libraries: [Library]
}

/// GET /api/books の1件(書誌のみ。スライド実体は deck.json / content.json)
struct Book: Codable, Identifiable, Hashable {
    let id: String
    let library: String?
    let title: String
    let authors: [String]?
    let narrators: [String]?
    let tags: [String]?
    let series: SeriesRef?
    let slides: Int?
    let duration: Double?
    let cover: String?
    let description: String?

    struct SeriesRef: Codable, Hashable {
        let name: String?
        // サーバは巻数を文字列(または null)で返す("2" のほか "上" 等もあり得る)
        let sequence: String?
    }
}

struct BooksResponse: Codable {
    let books: [Book]
}

struct BookResponse: Codable {
    let book: Book
}

/// deck.json(構造データ。cuesが章送りとスライド同期の軸。v1/v2互換)
struct Deck: Codable {
    let kikimiru: Int?
    let title: String?
    let audio: AudioRef
    let slides: [SlideRef]
    let cues: [Cue]

    struct AudioRef: Codable {
        let src: String
        let duration: Double?
    }

    struct SlideRef: Codable {
        let id: String
        let kind: String?
        let image: String?
    }

    struct Cue: Codable {
        let t: Double
        let slide: String?
    }
}

/// content.json(本文データ。スライドidごとのテキスト面。ファイル自体が任意)
struct ContentDoc: Codable {
    let kikimiru: Int?
    let slides: [String: SlideText]

    struct SlideText: Codable {
        let title: String?
        let bullets: [String]?
        let note: String?
        let alt: String?
    }
}

/// 再生進捗(サーバとLast-Write-Winsでマージする。atはミリ秒エポック)
struct ProgressRecord: Codable, Hashable {
    var t: Double
    var d: Double?
    var at: Double
    var s: Int?
    var n: Int?
}

struct ProgressResponse: Codable {
    var progress: [String: ProgressRecord]
}
