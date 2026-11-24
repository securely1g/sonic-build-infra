#[test]
fn read_process_information() {
    let system = sysinfo::System::new_all().expect("read system information");
    let process = procfs::process::Process::myself().expect("read this process");
    assert_eq!(process.pid, std::process::id() as i32);
    assert!(system
        .process(sysinfo::get_current_pid().unwrap())
        .is_some());
    assert!(regex::Regex::new(r"^\d+$")
        .unwrap()
        .is_match(&process.pid.to_string()));
}
