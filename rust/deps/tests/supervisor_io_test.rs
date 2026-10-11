use mio::{Events, Interest, Poll, Token};
use nix::unistd::{close, pipe, write};
use std::time::Duration;

#[test]
fn polling_observes_a_ready_pipe_and_signal_zero_keeps_the_process_alive() {
    let (reader, writer) = pipe().unwrap();
    let _reader = scopeguard::guard(reader, |fd| { close(fd).unwrap(); });
    let _writer = scopeguard::guard(writer, |fd| { close(fd).unwrap(); });
    let mut poll = Poll::new().unwrap();
    poll.registry()
        .register(&mut mio::unix::SourceFd(&reader), Token(7), Interest::READABLE)
        .unwrap();
    write(writer, b"event").unwrap();
    let mut events = Events::with_capacity(1);
    poll.poll(&mut events, Some(Duration::from_secs(1))).unwrap();
    assert!(events.iter().any(|event| event.token() == Token(7) && event.is_readable()));
    nix::sys::signal::kill(nix::unistd::getpid(), None).unwrap();
}

#[inline(never)]
fn intercepted_value() -> usize { 7 }

#[test]
fn the_pinned_injector_replaces_and_restores_a_function() {
    use injectorpp::interface::injector::*;
    assert_eq!(intercepted_value(), 7);
    {
        let mut injector = InjectorPP::new();
        injector
            .when_called(injectorpp::func!(fn (intercepted_value)() -> usize))
            .will_execute(injectorpp::fake!(func_type: fn() -> usize, returns: 42));
        assert_eq!(intercepted_value(), 42);
    }
    assert_eq!(intercepted_value(), 7);
}

#[test]
fn syslog_writer_initialization_does_not_require_a_running_daemon() {
    let writer = syslog_tracing::Syslog::new(
        c"supervisor-dependency-test",
        syslog_tracing::Options::LOG_PID,
        syslog_tracing::Facility::Daemon,
    );
    assert!(writer.is_some());
}
