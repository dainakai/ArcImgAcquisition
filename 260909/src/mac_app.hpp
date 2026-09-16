#pragma once
#include <string>

namespace holo {
std::string macBundledConfig();
std::string macCaptureRoot();
void macShowError(const std::string& message);
}
