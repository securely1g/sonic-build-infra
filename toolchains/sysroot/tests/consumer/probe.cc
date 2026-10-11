#include <iostream>
#include <stdexcept>
#include <string>

int shared_value();
void shared_throw();

int main() {
    if (shared_value() != 42) return 1;
    try {
        shared_throw();
        return 2;
    } catch (const std::runtime_error& error) {
        if (std::string(error.what()) != "shared sysroot exception") return 3;
    }
    std::cout << "SONiC shared sysroot 42 " << sizeof(void*) << '\n';
}
