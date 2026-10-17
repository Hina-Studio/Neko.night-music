#pragma once

#include <string>
#include <vector>

namespace ncm {

// Captures the default output device (WASAPI loopback) and exposes a smoothed
// 64-band magnitude snapshot for visualisation. Windows only.
class Spectrum {
 public:
  static constexpr int kBandCount = 64;

  Spectrum();
  ~Spectrum();

  Spectrum(const Spectrum&) = delete;
  Spectrum& operator=(const Spectrum&) = delete;

  // Returns kBandCount values in [0, 1]; all zeros when audio is unavailable.
  std::vector<float> snapshot();

  // Diagnostic text: "idle", "started" or "failed: <step> hr=0x...".
  std::string state() const;

 private:
  struct Impl;
  Impl* impl_ = nullptr;
};

}  // namespace ncm
