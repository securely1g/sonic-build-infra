use std::io::Write;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

#[test]
fn an_epoll_listener_receives_the_registered_event() {
    let (reader, mut writer) = UnixStream::pair().unwrap();
    let raw = epoll::create(true).unwrap();
    // epoll::create returns a new descriptor owned by the caller.
    let poll = unsafe { OwnedFd::from_raw_fd(raw) };
    let event = epoll::Event::new(epoll::Events::EPOLLIN, 7);
    epoll::ctl(
        poll.as_raw_fd(),
        epoll::ControlOptions::EPOLL_CTL_ADD,
        reader.as_raw_fd(),
        event,
    )
    .unwrap();
    writer.write_all(b"ready").unwrap();
    let mut received = [epoll::Event::new(epoll::Events::empty(), 0)];
    assert_eq!(
        epoll::wait(poll.as_raw_fd(), 1000, &mut received).unwrap(),
        1
    );
    // Copy out of the packed kernel structure before taking a reference.
    let token = received[0].data;
    assert_eq!(token, 7);
}

#[test]
fn linkat_publishes_a_configuration_file() {
    use rustix::fs::{linkat, AtFlags, CWD};
    let directory = PathBuf::from(std::env::var_os("TEST_TMPDIR").unwrap());
    let original = directory.join("rsyslog.conf.new");
    let installed = directory.join("rsyslog.conf");
    std::fs::write(&original, b"test configuration").unwrap();
    linkat(CWD, &original, CWD, &installed, AtFlags::empty()).unwrap();
    assert_eq!(std::fs::read(installed).unwrap(), b"test configuration");
}
