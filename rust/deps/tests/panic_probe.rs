struct DropMarker;

impl Drop for DropMarker {
    fn drop(&mut self) {
        std::fs::write(std::env::var("PANIC_DROP_FILE").unwrap(), "unwound").unwrap();
    }
}

fn main() {
    let value = panic_rust::linked_value();
    assert_eq!(value, 49);
    println!("cpp={value}");
    let _marker = DropMarker;
    panic!("controlled panic");
}
