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

#[test]
fn derive_input_and_serializer_use_the_dash_ha_apis() {
    #[derive(Debug, thiserror::Error)]
    #[error("unsupported field")]
    struct UnsupportedField;

    struct FieldSerializer;
    impl serde::Serializer for FieldSerializer {
        type Ok = ();
        type Error = serde::de::value::Error;
        serde_serializer_quick_unsupported::serializer_unsupported! {
            err = (<Self::Error as serde::ser::Error>::custom(UnsupportedField));
            bool i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 char str bytes none some unit
            unit_struct unit_variant newtype_struct newtype_variant seq tuple tuple_struct
            tuple_variant map struct struct_variant i128 u128
        }
    }

    let input: syn::DeriveInput = syn::parse_str("struct Scope;").unwrap();
    assert_eq!(input.ident, "Scope");
    let err = true.serialize(FieldSerializer).unwrap_err();
    assert_eq!(err.to_string(), "unsupported field");
}
