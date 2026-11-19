//! Repeatable core measurement; excludes rendering, plugins and OS delivery.
use std::{hint::black_box, time::Instant};
use zinput::ime::ContextId;
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine};
fn main() {
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(
        &mut storage,
        Engine::named("pinyin", None).unwrap(),
        ContextId::new(1).unwrap(),
    )
    .unwrap();
    let inputs = ["nihao", "shi", "zhongwen", "xiexie"];
    for index in 0..1000 {
        session.set_input(black_box(inputs[index % 4])).unwrap();
    }
    let mut samples = Vec::with_capacity(100_000);
    let start = Instant::now();
    for index in 0..100_000 {
        let sample = Instant::now();
        session.set_input(black_box(inputs[index % 4])).unwrap();
        black_box(session.candidates().get(0));
        samples.push(sample.elapsed().as_nanos());
    }
    let total = start.elapsed().as_nanos();
    samples.sort_unstable();
    println!(
        "{{\"operation\":\"zinput composition + starter-table lookup\",\"iterations\":100000,\"p50_ns\":{},\"p95_ns\":{},\"p99_ns\":{},\"total_ns\":{},\"excludes\":\"rendering, plugin IPC, OS delivery\"}}",
        samples[50_000], samples[95_000], samples[99_000], total
    );
}
