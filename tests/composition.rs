use zinput::ime::{ContextId, TextTarget};
use ztype::{
    MAX_TEXT_BYTES, Session,
    engine::{Candidates, Engine, Lexicon, romaji},
};

#[derive(Default)]
struct Sink {
    calls: Vec<(ContextId, String)>,
    fail: bool,
}
impl TextTarget for Sink {
    type Error = &'static str;
    fn commit_text(&mut self, id: ContextId, text: &str) -> Result<(), Self::Error> {
        if self.fail {
            return Err("offline");
        }
        self.calls.push((id, text.to_owned()));
        Ok(())
    }
}
fn id(value: u64) -> ContextId {
    ContextId::new(value).unwrap()
}

#[test]
fn exact_unicode_reaches_bound_context_once() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, Engine::Literal, id(41)).unwrap();
    let text = "你好・こんにちは・مرحبا・नमस्ते・🙂";
    session.set_input(text).unwrap();
    let mut sink = Sink::default();
    assert_eq!(session.commit(&mut sink).unwrap(), text.len());
    assert_eq!(sink.calls, vec![(id(41), text.into())]);
    assert!(session.commit(&mut sink).is_err());
    assert_eq!(sink.calls.len(), 1);
}

#[test]
fn focus_change_and_cancel_cannot_deliver_old_text() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, Engine::Literal, id(1)).unwrap();
    let mut sink = Sink::default();
    session.set_input("old").unwrap();
    session.set_context(id(2)).unwrap();
    assert!(session.commit(&mut sink).is_err());
    assert!(session.input().is_empty());
    session.set_input("cancelled").unwrap();
    session.clear().unwrap();
    assert!(session.commit(&mut sink).is_err());
    assert!(sink.calls.is_empty());
    session.set_input("new").unwrap();
    session.commit(&mut sink).unwrap();
    assert_eq!(sink.calls, vec![(id(2), "new".into())]);
}

#[test]
fn failed_target_keeps_editable_composition_without_automatic_retry() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, Engine::Literal, id(1)).unwrap();
    session.set_input("retry explicitly").unwrap();
    let mut sink = Sink {
        fail: true,
        ..Sink::default()
    };
    assert!(session.commit(&mut sink).is_err());
    assert_eq!(session.input(), "retry explicitly");
    assert_eq!(session.candidates().get(0), Some("retry explicitly"));
    sink.fail = false;
    session.set_input("edited instead").unwrap();
    session.commit(&mut sink).unwrap();
    assert_eq!(sink.calls, vec![(id(1), "edited instead".into())]);
}

#[test]
fn invalid_edits_preserve_displayed_candidate_and_input() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, Engine::Unicode, id(1)).unwrap();
    session.set_input("U+4F60").unwrap();
    for input in ["U+D800", "U+110000", "U+0000", "U+xyz", "hello\0"] {
        assert!(session.set_input(input).is_err(), "{input:?}");
        assert_eq!(session.input(), "U+4F60");
        assert_eq!(session.candidates().get(0), Some("你"));
    }
    assert!(session.set_input(&"x".repeat(MAX_TEXT_BYTES + 1)).is_err());
    assert_eq!(session.candidates().get(0), Some("你"));
}

#[test]
fn dictionary_priorities_prefixes_deduplication_and_unicode() {
    let table = Lexicon::parse(
        "hello\tمرحبا\t20\nhello\t你好\t99\nhelloworld\t世界\t100\nhello\t你好\t90\n",
    )
    .unwrap();
    let mut engine = Engine::Table(table);
    let mut out = Candidates::new();
    engine.candidates("HELLO", &mut out).unwrap();
    assert_eq!(
        out.iter().collect::<Vec<_>>(),
        vec!["你好", "مرحبا", "世界"]
    );
    engine.candidates("unknown", &mut out).unwrap();
    assert_eq!(out.iter().collect::<Vec<_>>(), vec!["unknown"]);
    engine.candidates("", &mut out).unwrap();
    assert!(out.is_empty());
}

#[test]
fn dictionary_rejects_bad_structure_and_unbounded_entries() {
    for input in [
        "",
        "# empty",
        "hello",
        "hi\t",
        "HI\t你好",
        "hi\t你好\t-1",
        "hi\t你好\t1\textra",
        "hi\tbad\0",
    ] {
        assert!(Lexicon::parse(input).is_err(), "{input:?}");
    }
    assert!(Lexicon::parse(&format!("hi\t{}", "中".repeat(1400))).is_err());
}

#[test]
fn shipped_dictionary_commits_nonfirst_candidate() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session =
        Session::new(&mut storage, Engine::named("pinyin", None).unwrap(), id(1)).unwrap();
    session.set_input("ta").unwrap();
    session.select(1).unwrap();
    let mut sink = Sink::default();
    session.commit(&mut sink).unwrap();
    assert_eq!(sink.calls[0].1, "她");
}

#[test]
fn kana_handles_n_gemination_digraphs_and_unicode_boundaries() {
    for (input, expected) in [
        ("konnichiha", "こんにちは"),
        ("gakkou", "がっこう"),
        ("shinjuku", "しんじゅく"),
        ("kan'i", "かんい"),
        ("nn", "ん"),
        ("nna", "んな"),
        ("kya", "きゃ"),
        ("maccha", "まっちゃ"),
        ("テスト🙂", "テスト🙂"),
        ("xtsu", "っ"),
        ("nihongo.", "にほんご。"),
    ] {
        assert_eq!(romaji(input).unwrap(), expected, "{input}");
    }
    assert!(romaji(&"a".repeat(MAX_TEXT_BYTES)).is_err());
    let mut engine = Engine::Kana;
    let mut out = Candidates::new();
    engine.candidates("sushi", &mut out).unwrap();
    assert_eq!(
        out.iter().collect::<Vec<_>>(),
        vec!["すし", "スシ", "sushi"]
    );
}

#[test]
fn unicode_covers_supplementary_and_combining_scalars() {
    let mut engine = Engine::Unicode;
    let mut out = Candidates::new();
    engine
        .candidates("U+1F642 0065 0301 1F1E8 1F1F3", &mut out)
        .unwrap();
    assert_eq!(out.get(0), Some("🙂e\u{301}🇨🇳"));
    for input in [
        "D800", "DFFF", "110000", "-1", "U+", "U+7f", "U+a", "FFFFFFFF",
    ] {
        assert!(engine.candidates(input, &mut out).is_err(), "{input}");
    }
}

#[test]
fn bounded_candidate_count_and_storage_boundaries() {
    let table = (0..30)
        .map(|i| format!("word\tvalue-{i}\t{i}\n"))
        .collect::<String>();
    let mut engine = Engine::Table(Lexicon::parse(&table).unwrap());
    let mut out = Candidates::new();
    engine.candidates("word", &mut out).unwrap();
    assert_eq!(out.len(), 9);
    assert_eq!(out.get(0), Some("value-29"));
    let mut storage = [0; 3];
    let mut session = Session::new(&mut storage, Engine::Literal, id(1)).unwrap();
    session.set_input("中").unwrap();
    assert!(session.set_input("🙂").is_err());
    assert_eq!(session.input(), "中");
}
