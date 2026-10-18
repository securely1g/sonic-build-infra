#include <iostream>
#include <string>
#include <vector>

int main() {
    std::vector<std::string> words{"SONiC", "Clang", "sysroot"};
    for (const auto& word : words) std::cout << word << ' ';
    std::cout << sizeof(void*) << '\n';
}
