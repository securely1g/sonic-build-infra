extern "C" {
    fn cpp_value() -> i32;
    fn native_value() -> i32;
}

pub fn linked_value() -> i32 {
    unsafe { cpp_value() + native_value() }
}
