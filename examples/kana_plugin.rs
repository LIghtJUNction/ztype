//! A real native language plugin built from the same engine as the application.
use zinput::engine::InputEngineExport;
use ztype::{engine::Engine, plugin::BuiltinEngine};

fn create() -> Result<BuiltinEngine, zplugin::Error> {
    Ok(BuiltinEngine::new(Engine::Kana))
}
zplugin::export!(
    InputEngineExport<BuiltinEngine>,
    constructor = create,
    version = (0, 1, 0),
    unload = pinned
);
