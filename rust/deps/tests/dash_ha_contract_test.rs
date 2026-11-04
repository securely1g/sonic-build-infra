use dashmap::DashMap;
use enumset::{EnumSet, EnumSetType};
use prost::Message;
use serde::{Deserialize, Serialize};
use serde_with::{serde_as, DisplayFromStr};

#[derive(Clone, PartialEq, Message)]
struct State {
    #[prost(string, tag = "1")]
    name: String,
}
#[serde_as]
#[derive(Debug, PartialEq, Serialize, Deserialize)]
struct Scope {
    #[serde_as(as = "DisplayFromStr")]
    id: u64,
}
#[derive(EnumSetType, Debug)]
enum Role {
    Active,
    Standby,
}

#[test]
fn protocols_and_state_share_the_public_crates() {
    let state = State {
        name: "DASH_HA_SCOPE_TABLE".into(),
    };
    let bytes = state.encode_to_vec();
    let request = tonic::Request::new(State::decode(bytes.as_slice()).unwrap());
    assert_eq!(request.get_ref(), &state);
    let map = DashMap::new();
    map.insert(7, serde_json::to_string(&Scope { id: 7 }).unwrap());
    assert_eq!(*map.get(&7).unwrap(), r#"{"id":"7"}"#);
    assert_eq!(
        serde_json::from_str::<Scope>(&map.get(&7).unwrap()).unwrap(),
        Scope { id: 7 }
    );
    let roles = EnumSet::only(Role::Active) | Role::Standby;
    assert_eq!(roles.len(), 2);
    // The source owner's unchanged build.rs programs consume these interfaces.
    // Full generation is exercised by that owner's declared generator actions.
    let mut prost = prost_build::Config::new();
    prost.type_attribute(
        "dash.ha_scope_config.HaScopeConfig",
        "#[derive(serde::Serialize)]",
    );
    let _tonic = tonic_build::configure()
        .build_server(true)
        .build_client(true);
}

#[tokio::test]
async fn cancellation_uses_the_selected_shared_runtime() {
    let token = tokio_util::sync::CancellationToken::new();
    let child = token.child_token();
    let tracker = tokio_util::task::TaskTracker::new();
    let completed = tracker.spawn(async move {
        child.cancelled().await;
        42
    });
    token.cancel();
    tracker.close();
    tracker.wait().await;
    assert_eq!(completed.await.unwrap(), 42);
}
