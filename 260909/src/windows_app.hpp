#pragma once
#include <string>

namespace holo {
std::string windowsCaptureRoot();
void windowsReportError(const std::string& message,bool show_dialog);
}
