#![cfg(feature = "plugins")]
use zinput::engine::{InputEngine, change, key, phase};
use zplugin::Error;
use ztype::{engine::Engine, plugin::BuiltinEngine};

#[test]
fn bounded_plugin_writes_and_pending_commit_are_transactional() {
    let mut engine = BuiltinEngine::new(Engine::Kana);
    assert_eq!(engine.process(phase::PRESS, key::TEXT, 0, "sushi"), Ok(0));
    engine.reset(9).unwrap();
    assert_ne!(
        engine.process(phase::PRESS, key::TEXT, 0, "sushi").unwrap() & change::CANDIDATES,
        0
    );
    let mut short = [0xa5; 2];
    assert_eq!(engine.candidate(0, &mut short), Err(Error::BufferTooSmall));
    assert_eq!(short, [0xa5; 2]);
    assert_ne!(engine.select(1).unwrap() & change::COMMIT, 0);
    assert_eq!(
        engine.process(phase::PRESS, key::TEXT, 0, "next"),
        Err(Error::Busy)
    );
    assert_eq!(engine.commit(&mut short), Err(Error::BufferTooSmall));
    assert_eq!(short, [0xa5; 2]);
    let mut output = [0; 64];
    let size = engine.commit(&mut output).unwrap() as usize;
    assert_eq!(std::str::from_utf8(&output[..size]).unwrap(), "スシ");
    assert_eq!(engine.commit(&mut output), Ok(0));
    assert!(engine.process(phase::PRESS, key::TEXT, 0, "new").is_ok());
}

#[test]
fn plugin_blur_clears_composition_and_pending_commit() {
    let mut engine = BuiltinEngine::new(Engine::Literal);
    engine.reset(4).unwrap();
    engine
        .process(phase::PRESS, key::TEXT, 0, "private")
        .unwrap();
    engine.select(0).unwrap();
    engine.reset(0).unwrap();
    let mut output = [0; 64];
    assert_eq!(engine.commit(&mut output), Ok(0));
    assert_eq!(engine.preedit(&mut output), Ok(0));
    assert_eq!(engine.candidate_count(), Ok(0));
    assert!(engine.process(999, key::TEXT, 0, "bad").is_err());
    assert_eq!(
        engine.process(phase::RELEASE, key::TEXT, 0, "ignored"),
        Ok(0)
    );
}

#[test]
fn plugin_edits_respect_unicode_and_byte_selection() {
    let mut engine = BuiltinEngine::new(Engine::Literal);
    engine.reset(1).unwrap();
    engine
        .process(phase::PRESS, key::TEXT, 0, "你好🙂")
        .unwrap();
    assert_eq!(engine.selection().unwrap().end, 10);
    engine.process(phase::PRESS, key::BACKSPACE, 0, "").unwrap();
    let mut out = [0; 64];
    let size = engine.preedit(&mut out).unwrap() as usize;
    assert_eq!(std::str::from_utf8(&out[..size]).unwrap(), "你好");
    assert!(
        engine
            .process(phase::PRESS, key::TEXT, 0, &"x".repeat(4097))
            .is_err()
    );
    let size = engine.preedit(&mut out).unwrap() as usize;
    assert_eq!(std::str::from_utf8(&out[..size]).unwrap(), "你好");
}
