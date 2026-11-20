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
    assert_eq!(out.len(), 30);
    assert_eq!(out.get(0), Some("value-29"));
    let mut storage = [0; 3];
    let mut session = Session::new(&mut storage, Engine::Literal, id(1)).unwrap();
    session.set_input("中").unwrap();
    assert!(session.set_input("🙂").is_err());
    assert_eq!(session.input(), "中");
}

#[test]
fn pinyin_composes_complete_words_from_the_starter_table() {
    let mut engine = Engine::named("pinyin", None).unwrap();
    let mut out = Candidates::new();
    for (input, expected) in [
        ("nihaoshijie", "你好世界"),
        ("zhongwenshurufa", "中文输入法"),
        ("womengongzuo", "我们工作"),
        ("jintianxuexi", "今天学习"),
        ("NI HAO SHI JIE", "你好世界"),
        ("ni'hao'shijie", "你好世界"),
    ] {
        engine.candidates(input, &mut out).unwrap();
        assert_eq!(out.get(0), Some(expected), "{input}");
    }
}

#[test]
fn pinyin_starter_vocabulary_composes_everyday_input_sentences() {
    let mut engine = Engine::named("pinyin", None).unwrap();
    let mut out = Candidates::new();
    for (input, expected) in [
        ("woxiangceshishuruxiaoguo", "我想测试输入效果"),
        ("jixushiyongzhongwenshurufa", "继续使用中文输入法"),
        ("woxiangtiyanzhongwenshurufa", "我想体验中文输入法"),
        ("jintianceshishunli", "今天测试顺利"),
        ("qingdakaishurukuang", "请打开输入框"),
        ("wohenxihuanzhegexiaoguo", "我很喜欢这个效果"),
    ] {
        engine.candidates(input, &mut out).unwrap();
        assert_eq!(out.get(0), Some(expected), "{input}");
    }
}

#[test]
fn pinyin_trial_sentences_compose_incrementally_before_commit() {
    use ztype::input_method::{InputMethod, Key};

    let mut storage = [0; 4000];
    let mut ime = InputMethod::new(&mut storage, Engine::named("pinyin", None).unwrap()).unwrap();
    for (input, expected) in [
        ("woxiangceshishuruxiaoguo", "我想测试输入效果"),
        ("jixushiyongzhongwenshurufa", "继续使用中文输入法"),
    ] {
        for ch in input.chars() {
            let mut utf8 = [0; 4];
            let action = ime.key(Key::Text(ch.encode_utf8(&mut utf8)), 0).unwrap();
            assert!(action.consumed);
            assert!(action.commit.is_none());
        }
        assert_eq!(ime.input(), input);
        assert_eq!(ime.preedit(), expected);
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some(expected)
        );
        assert!(ime.input().is_empty());
    }
}

#[test]
fn pinyin_trial_vocabulary_keeps_unknown_sentence_suffixes_literal() {
    let mut engine = Engine::named("pinyin", None).unwrap();
    let mut out = Candidates::new();
    for input in [
        "woxiangceshishuruxiaoguoxyz",
        "jixushiyongzhongwenshurufaxyz",
        "woxiangceshi'unknown",
        "WO XIANG CESHI SHURU XIAOGUO UNKNOWN",
    ] {
        engine.candidates(input, &mut out).unwrap();
        assert_eq!(out.iter().collect::<Vec<_>>(), vec![input], "{input}");
    }
}

#[test]
fn pinyin_separators_disambiguate_without_changing_generic_tables() {
    let dictionary = "xi\t西\t100\nan\t安\t100\nxian\t先\t100\n";
    let mut pinyin = Engine::named("pinyin", Some(dictionary)).unwrap();
    let mut table = Engine::named("table", Some(dictionary)).unwrap();
    let mut out = Candidates::new();
    pinyin.candidates("xian", &mut out).unwrap();
    assert_eq!(out.get(0), Some("先"));
    pinyin.candidates("xi'an", &mut out).unwrap();
    assert_eq!(out.get(0), Some("西安"));
    table.candidates("xi'an", &mut out).unwrap();
    assert_eq!(out.get(0), Some("xi'an"));
    for input in ["'xian", "xian'", "xi''an", "xi'unknown"] {
        pinyin.candidates(input, &mut out).unwrap();
        assert_eq!(out.iter().collect::<Vec<_>>(), vec![input], "{input}");
    }
}

#[test]
fn pinyin_prefers_whole_phrases_then_fewer_words_then_priority() {
    let dictionary = "a\tA\t200\nab\tX\t1\nb\tB\t200\nbc\tY\t100\nc\tC\t200\n";
    let mut engine = Engine::named("pinyin", Some(dictionary)).unwrap();
    let mut out = Candidates::new();
    engine.candidates("ab", &mut out).unwrap();
    assert_eq!(out.get(0), Some("X"));
    engine.candidates("abc", &mut out).unwrap();
    assert_eq!(out.get(0), Some("AY"));
    engine.candidates("abcabc", &mut out).unwrap();
    assert_eq!(out.get(0), Some("AYAY"));

    let mut engine = Engine::named("pinyin", None).unwrap();
    engine.candidates("ta", &mut out).unwrap();
    assert_eq!(
        out.iter().take(3).collect::<Vec<_>>(),
        vec!["他", "她", "它"]
    );
    assert!(out.len() <= ztype::MAX_CANDIDATES);
}

#[test]
fn pinyin_preserves_every_byte_of_unknown_or_mixed_input() {
    let mut engine = Engine::named("pinyin", None).unwrap();
    let mut out = Candidates::new();
    for input in [
        "nihaoxyz",
        "nihao🙂",
        "nihao123",
        "ni'xyz",
        "NI HAO UNKNOWN",
    ] {
        engine.candidates(input, &mut out).unwrap();
        assert_eq!(out.iter().collect::<Vec<_>>(), vec![input], "{input}");
    }
}

#[test]
fn pinyin_composition_respects_utf8_output_and_input_limits() {
    let mut engine = Engine::named("pinyin", None).unwrap();
    let mut out = Candidates::new();
    let fitting = "ni".repeat(MAX_TEXT_BYTES / 3);
    engine.candidates(&fitting, &mut out).unwrap();
    assert_eq!(out.get(0), Some("你".repeat(MAX_TEXT_BYTES / 3).as_str()));
    assert_eq!(out.get(0).unwrap().len(), 4095);

    let overflow = "ni".repeat(MAX_TEXT_BYTES / 2);
    engine.candidates(&overflow, &mut out).unwrap();
    assert_eq!(out.get(0), Some(overflow.as_str()));
    assert_eq!(out.get(0).unwrap().len(), MAX_TEXT_BYTES);

    assert!(
        engine
            .candidates(&"a".repeat(MAX_TEXT_BYTES + 1), &mut out)
            .is_err()
    );
}

#[test]
fn composed_pinyin_commits_once_to_the_current_context() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session =
        Session::new(&mut storage, Engine::named("pinyin", None).unwrap(), id(7)).unwrap();
    session.set_input("nihaoshijie").unwrap();
    let mut sink = Sink::default();
    session.commit(&mut sink).unwrap();
    assert_eq!(sink.calls, vec![(id(7), "你好世界".into())]);
    assert!(session.commit(&mut sink).is_err());
    assert_eq!(sink.calls.len(), 1);
}
