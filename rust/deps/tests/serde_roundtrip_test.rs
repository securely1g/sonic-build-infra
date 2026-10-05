use serde::{Deserialize, Serialize};
use std::rc::Rc;

#[derive(Debug, PartialEq, Serialize, Deserialize)]
struct Update {
    key: Rc<String>,
    packets: u64,
}

#[test]
fn common_serde_features_work_with_swss_json() {
    let update = Update {
        key: Rc::new("Ethernet0".into()),
        packets: 42,
    };
    let json = serde_json::to_string(&update).unwrap();
    assert_eq!(serde_json::from_str::<Update>(&json).unwrap(), update);
}

#[test]
fn public_serde_core_traits_match_serde_derive() {
    fn implements_core_traits<T: serde_core::Serialize + serde_core::de::DeserializeOwned>() {}
    implements_core_traits::<Update>();
    implements_core_traits::<Rc<String>>();
}
