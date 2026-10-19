#include <atomic>
#include <stdexcept>
#include <thread>

#include "generated_value.h"

int shared_value() {
    std::atomic<int> value{0};
    std::thread worker([&value] {
        value.store(sonic_generated_value());
    });
    worker.join();
    return value.load();
}

void shared_throw() {
    throw std::runtime_error("shared sysroot exception");
}
