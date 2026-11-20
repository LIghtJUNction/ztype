use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use zinput::ime::{ContextId, TextTarget};
use ztype::{
    MAX_CANDIDATES, MAX_TEXT_BYTES, Session,
    dictionary::load_engine,
    engine::{Candidates, Engine, Lexicon},
};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "ztype-dictionary-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn file(&self, name: &str, data: &str) -> PathBuf {
        let path = self.0.join(name);
        std::fs::write(&path, data).unwrap();
        path
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[derive(Default)]
struct Sink {
    fail: bool,
    text: String,
}
impl TextTarget for Sink {
    type Error = &'static str;
    fn commit_text(&mut self, _: ContextId, text: &str) -> Result<(), Self::Error> {
        if self.fail {
            return Err("unavailable");
        }
        self.text.push_str(text);
        Ok(())
    }
}

#[test]
fn merges_vocabulary_and_ranks_prefixes_by_frequency() {
    let dir = Temp::new();
    let a = dir.file("a.tsv", "zha\t扎\t1\nzhongguo\t中国\t10000\n");
    let b = dir.file("b.tsv", "zhongwen\t中文\t50000\nzhongguo\t中国\t20000\n");
    let mut engine = load_engine("pinyin", &[a, b], None, false).unwrap();
    let mut out = Candidates::new();
    engine.candidates("zh", &mut out).unwrap();
    assert_eq!(out.get(0), Some("中文"));
    assert_eq!(out.get(1), Some("中国"));
    engine.candidates("nihao", &mut out).unwrap();
    assert_eq!(out.get(0), Some("你好"));
}

#[test]
fn candidates_extend_beyond_the_first_page_and_remain_bounded() {
    let tsv: String = (0..500).map(|i| format!("shi\t词{i}\t{i}\n")).collect();
    let mut engine = Engine::Pinyin(Lexicon::parse(&tsv).unwrap());
    let mut out = Candidates::new();
    engine.candidates("shi", &mut out).unwrap();
    assert_eq!(out.len(), MAX_CANDIDATES);
    assert_eq!(out.get(0), Some("词499"));
    assert_eq!(out.get(20), Some("词479"));
    assert_eq!(out.get(MAX_CANDIDATES - 1), Some("词372"));
}

#[test]
fn accepts_full_size_vocabulary_beyond_old_demo_limit() {
    let tsv: String = (0..110_000)
        .map(|i| format!("word{i:06}\t词{i}\t{i}\n"))
        .collect();
    let lexicon = Lexicon::parse(&tsv).unwrap();
    assert_eq!(lexicon.len(), 110_000);
}

#[test]
fn learning_disabled_never_creates_a_user_data_file() {
    let dir = Temp::new();
    let user = dir.0.join("not-created/user.tsv");
    let engine = load_engine("pinyin", &[], Some(&user), false).unwrap();
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, engine, ContextId::new(1).unwrap()).unwrap();
    session.set_input("nihao").unwrap();
    session.commit(&mut Sink::default()).unwrap();
    assert!(!user.parent().unwrap().exists());
    assert!(load_engine("pinyin", &[], None, true).is_err());
}

#[test]
fn explicit_learning_only_records_success_and_merges_two_services() {
    let dir = Temp::new();
    let user = dir.0.join("user.tsv");
    let first = load_engine("pinyin", &[], Some(&user), true).unwrap();
    let second = load_engine("pinyin", &[], Some(&user), true).unwrap();
    let mut storage_a = [0; MAX_TEXT_BYTES];
    let mut storage_b = [0; MAX_TEXT_BYTES];
    let mut a = Session::new(&mut storage_a, first, ContextId::new(1).unwrap()).unwrap();
    let mut b = Session::new(&mut storage_b, second, ContextId::new(2).unwrap()).unwrap();
    a.set_input("ta").unwrap();
    a.select(1).unwrap();
    assert!(
        a.commit(&mut Sink {
            fail: true,
            ..Sink::default()
        })
        .is_err()
    );
    assert!(!user.exists());
    a.commit(&mut Sink::default()).unwrap();
    b.set_input("nihao").unwrap();
    b.commit(&mut Sink::default()).unwrap();
    let saved = std::fs::read_to_string(&user).unwrap();
    assert!(saved.contains("ta\t她\t1\n"));
    assert!(saved.contains("nihao\t你好\t1\n"));
    let mut loaded = load_engine("pinyin", &[], Some(&user), false).unwrap();
    let mut out = Candidates::new();
    loaded.candidates("ta", &mut out).unwrap();
    assert_eq!(out.get(0), Some("她"));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(user).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[test]
fn umlaut_spellings_use_the_same_pinyin_vocabulary() {
    let mut engine = Engine::Pinyin(Lexicon::parse("lv\t吕\t1\n").unwrap());
    let mut out = Candidates::new();
    for spelling in ["lv", "lü", "LÜ", "lu:"] {
        engine.candidates(spelling, &mut out).unwrap();
        assert_eq!(out.get(0), Some("吕"));
    }
}

#[test]
fn sentence_ranking_does_not_join_a_rare_long_word_to_a_frequent_character() {
    let dictionary = "wo\t我\t100\nzai\t在\t100\ntushuguan\t图书馆\t1000\nxuexi\t学习\t1000\ntushuguanxue\t图书馆学\t1\nxi\t洗\t100000\n";
    let mut engine = Engine::Pinyin(Lexicon::parse(dictionary).unwrap());
    let mut out = Candidates::new();
    engine.candidates("wozaitushuguanxuexi", &mut out).unwrap();
    assert_eq!(out.get(0), Some("我在图书馆学习"));
}

#[test]
#[ignore = "requires ZTYPE_TEST_DICTIONARY pointing to the separately installed full vocabulary"]
fn installed_vocabulary_covers_ordinary_words_and_unseen_sentences() {
    let path = PathBuf::from(
        std::env::var_os("ZTYPE_TEST_DICTIONARY").expect("set ZTYPE_TEST_DICTIONARY"),
    );
    let load = std::time::Instant::now();
    let mut engine = load_engine("pinyin", &[path], None, false).unwrap();
    eprintln!(
        "dictionary entries={} load_ms={:.2}",
        engine.dictionary_entries(),
        load.elapsed().as_secs_f64() * 1000.
    );
    assert!(engine.dictionary_entries() > 500_000);
    let mut out = Candidates::new();
    let cases = [
        ("tushuguan", "图书馆"),
        ("ruanjian", "软件"),
        ("diannao", "电脑"),
        ("xuexiao", "学校"),
        ("tongzhi", "通知"),
        ("kaifazhe", "开发者"),
        ("dianhua", "电话"),
        ("xianzai", "现在"),
        ("shurufa", "输入法"),
        ("wozaitushuguanxuexi", "我在图书馆学习"),
        ("jintianwoxiangqubeijing", "今天我想去北京"),
    ];
    let mut timings = Vec::new();
    let mut key_timings = Vec::new();
    for (spelling, expected) in cases {
        for end in 1..=spelling.len() {
            let key_start = std::time::Instant::now();
            engine.candidates(&spelling[..end], &mut out).unwrap();
            key_timings.push(key_start.elapsed().as_secs_f64() * 1000.);
        }
        let start = std::time::Instant::now();
        engine.candidates(spelling, &mut out).unwrap();
        timings.push(start.elapsed().as_secs_f64() * 1000.);
        eprintln!("{spelling}: {:?}", out.iter().take(5).collect::<Vec<_>>());
        assert_eq!(out.get(0), Some(expected), "{spelling}");
    }
    timings.sort_by(f64::total_cmp);
    eprintln!(
        "full-query median_ms={:.3} max_ms={:.3}",
        timings[timings.len() / 2],
        timings[timings.len() - 1]
    );
    key_timings.sort_by(f64::total_cmp);
    eprintln!(
        "keystrokes={} median_ms={:.3} p95_ms={:.3} max_ms={:.3}",
        key_timings.len(),
        key_timings[key_timings.len() / 2],
        key_timings[key_timings.len() * 95 / 100],
        key_timings[key_timings.len() - 1]
    );
    #[cfg(target_os = "linux")]
    if let Ok(status) = std::fs::read_to_string("/proc/self/status") {
        for line in status
            .lines()
            .filter(|line| line.starts_with("VmRSS:") || line.starts_with("VmHWM:"))
        {
            eprintln!("{line}");
        }
    }
}
