use tracing_subscriber::prelude::*;

#[test]
fn shared_tracing_traits_accept_the_shared_subscriber() {
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new("info"))
        .with(tracing_subscriber::fmt::layer().with_test_writer());
    tracing::subscriber::with_default(subscriber, || {
        tracing::info!(packets = 42, "Ethernet0 counter update");
    });
}
