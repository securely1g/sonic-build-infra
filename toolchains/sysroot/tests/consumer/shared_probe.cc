#include <atomic>
#include <stdexcept>
#include <thread>

int shared_value() {
    std::atomic<int> value{0};
    std::thread worker([&value] { value.store(42); });
    worker.join();
    return value.load();
}

void shared_throw() {
    throw std::runtime_error("shared sysroot exception");
}
