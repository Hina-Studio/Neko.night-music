#include "ncm/spectrum.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <mmreg.h>

namespace ncm {
namespace {

std::string format_failure(const char* step, HRESULT hr) {
  char buffer[96];
  std::snprintf(buffer, sizeof(buffer), "failed: %s hr=0x%08lX", step,
                static_cast<unsigned long>(hr));
  return std::string(buffer);
}

constexpr int kFftSize = 2048;
constexpr size_t kRingSize = 8192;
constexpr double kPi = 3.14159265358979323846;

// {00000003-0000-0010-8000-00AA00389B71} - IEEE float sub-format.
const GUID kSubtypeFloat = {
    0x00000003, 0x0000, 0x0010,
    {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};

void fft(std::vector<std::complex<float>>& data) {
  const size_t n = data.size();
  for (size_t i = 1, j = 0; i < n; ++i) {
    size_t bit = n >> 1;
    for (; j & bit; bit >>= 1) {
      j ^= bit;
    }
    j ^= bit;
    if (i < j) {
      std::swap(data[i], data[j]);
    }
  }
  for (size_t len = 2; len <= n; len <<= 1) {
    const double angle = -2.0 * kPi / static_cast<double>(len);
    const std::complex<float> step(
        static_cast<float>(std::cos(angle)),
        static_cast<float>(std::sin(angle)));
    for (size_t start = 0; start < n; start += len) {
      std::complex<float> weight(1.0f, 0.0f);
      for (size_t offset = 0; offset < len / 2; ++offset) {
        const std::complex<float> even = data[start + offset];
        const std::complex<float> odd = data[start + offset + len / 2] * weight;
        data[start + offset] = even + odd;
        data[start + offset + len / 2] = even - odd;
        weight *= step;
      }
    }
  }
}

template <typename T>
void release(T*& pointer) {
  if (pointer != nullptr) {
    pointer->Release();
    pointer = nullptr;
  }
}

}  // namespace

struct Spectrum::Impl {
  bool com_initialized = false;
  bool started = false;
  bool failed = false;

  IMMDeviceEnumerator* enumerator = nullptr;
  IMMDevice* device = nullptr;
  IAudioClient* client = nullptr;
  IAudioCaptureClient* capture = nullptr;
  WAVEFORMATEX* format = nullptr;

  int channels = 2;
  int sample_rate = 48000;
  int bytes_per_sample = 4;
  bool is_float = true;

  std::vector<float> ring = std::vector<float>(kRingSize, 0.0f);
  size_t ring_pos = 0;
  bool ring_ready = false;

  std::vector<float> window;
  std::vector<float> bands = std::vector<float>(kBandCount, 0.0f);
  std::vector<std::complex<float>> buffer;
  std::string error;

  Impl() {
    window.resize(kFftSize);
    for (int i = 0; i < kFftSize; ++i) {
      window[i] = 0.5f - 0.5f * static_cast<float>(
                             std::cos(2.0 * kPi * i / (kFftSize - 1)));
    }
    buffer.resize(kFftSize);
  }

  ~Impl() { stop(); }

  float sample_at(const BYTE* data, UINT32 frame, int channel) const {
    const size_t offset =
        (static_cast<size_t>(frame) * channels + channel) * bytes_per_sample;
    const BYTE* p = data + offset;
    if (is_float && bytes_per_sample == 4) {
      float value;
      std::memcpy(&value, p, sizeof(value));
      return value;
    }
    if (bytes_per_sample == 2) {
      int16_t value;
      std::memcpy(&value, p, sizeof(value));
      return static_cast<float>(value) / 32768.0f;
    }
    return 0.0f;
  }

  void start() {
    if (started || failed) {
      return;
    }
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (SUCCEEDED(hr)) {
      com_initialized = true;
    } else if (hr != RPC_E_CHANGED_MODE) {
      failed = true;
      error = format_failure("CoInitializeEx", hr);
      return;
    }

    hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                          __uuidof(IMMDeviceEnumerator),
                          reinterpret_cast<void**>(&enumerator));
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("CoCreateInstance", hr);
      return;
    }
    hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("GetDefaultAudioEndpoint", hr);
      return;
    }
    hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                          reinterpret_cast<void**>(&client));
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("Activate", hr);
      return;
    }
    hr = client->GetMixFormat(&format);
    if (FAILED(hr) || format == nullptr) {
      failed = true;
      error = format_failure("GetMixFormat", hr);
      return;
    }

    const REFERENCE_TIME buffer_duration = 1000000;  // 100 ms
    hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                            AUDCLNT_STREAMFLAGS_LOOPBACK, buffer_duration, 0,
                            format, nullptr);
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("Initialize", hr);
      return;
    }
    hr = client->GetService(__uuidof(IAudioCaptureClient),
                            reinterpret_cast<void**>(&capture));
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("GetService", hr);
      return;
    }

    channels = format->nChannels > 0 ? format->nChannels : 2;
    sample_rate = format->nSamplesPerSec > 0 ? format->nSamplesPerSec : 48000;
    bytes_per_sample = format->wBitsPerSample / 8;
    is_float = format->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
    if (format->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
      const auto* extended =
          reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(format);
      is_float = IsEqualGUID(extended->SubFormat, kSubtypeFloat) != 0;
    }
    if (bytes_per_sample != 4 && bytes_per_sample != 2) {
      failed = true;
      error = "failed: unsupported sample format";
      return;
    }

    hr = client->Start();
    if (FAILED(hr)) {
      failed = true;
      error = format_failure("Start", hr);
      return;
    }
    started = true;
  }

  void stop() {
    if (client != nullptr && started) {
      client->Stop();
    }
    started = false;
    release(capture);
    release(client);
    release(device);
    release(enumerator);
    if (format != nullptr) {
      CoTaskMemFree(format);
      format = nullptr;
    }
    if (com_initialized) {
      CoUninitialize();
      com_initialized = false;
    }
  }

  void capture_audio() {
    UINT32 packet = 0;
    if (FAILED(capture->GetNextPacketSize(&packet))) {
      return;
    }
    while (packet != 0) {
      BYTE* data = nullptr;
      UINT32 frames = 0;
      DWORD flags = 0;
      if (FAILED(capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr))) {
        return;
      }
      const bool silent = (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0;
      for (UINT32 frame = 0; frame < frames; ++frame) {
        float value = 0.0f;
        if (!silent && data != nullptr) {
          float sum = 0.0f;
          for (int channel = 0; channel < channels; ++channel) {
            sum += sample_at(data, frame, channel);
          }
          value = sum / static_cast<float>(channels);
        }
        ring[ring_pos] = value;
        ring_pos = (ring_pos + 1) % ring.size();
      }
      if (frames > 0) {
        ring_ready = true;
      }
      capture->ReleaseBuffer(frames);
      if (FAILED(capture->GetNextPacketSize(&packet))) {
        return;
      }
    }
  }

  void analyse() {
    for (int i = 0; i < kFftSize; ++i) {
      float sample = 0.0f;
      if (ring_ready) {
        const size_t index =
            (ring_pos + ring.size() - kFftSize + i) % ring.size();
        sample = ring[index];
      }
      buffer[i] = std::complex<float>(sample * window[i], 0.0f);
    }
    fft(buffer);

    const double nyquist = sample_rate / 2.0;
    const double low_hz = 40.0;
    const double high_hz = std::min(16000.0, nyquist);
    const double log_low = std::log(low_hz);
    const double log_high = std::log(high_hz);
    const float normalize = 1.0f / static_cast<float>(kFftSize);

    for (int band = 0; band < kBandCount; ++band) {
      const double ratio_low = static_cast<double>(band) / kBandCount;
      const double ratio_high = static_cast<double>(band + 1) / kBandCount;
      const int bin_low = std::max(
          1, static_cast<int>(std::exp(log_low + (log_high - log_low) *
                                                    ratio_low) /
                              nyquist * (kFftSize / 2)));
      const int bin_high = std::max(
          bin_low + 1,
          static_cast<int>(std::exp(log_low + (log_high - log_low) * ratio_high) /
                           nyquist * (kFftSize / 2)));
      float peak = 0.0f;
      const int limit = std::min(bin_high, kFftSize / 2);
      for (int bin = bin_low; bin < limit; ++bin) {
        peak = std::max(peak, std::abs(buffer[bin]) * normalize);
      }
      const float db = 20.0f * std::log10(peak + 1e-7f);
      float level = (db + 72.0f) / 72.0f;
      level = std::clamp(level, 0.0f, 1.0f);
      // Slight high-frequency tilt so the right half is not visually dead.
      level *= 0.72f + 0.55f * static_cast<float>(band) / kBandCount;
      level = std::clamp(level, 0.0f, 1.0f);

      float& smoothed = bands[band];
      if (level > smoothed) {
        smoothed = level;
      } else {
        smoothed = smoothed * 0.80f + level * 0.20f;
      }
    }
  }

  std::vector<float> snapshot_impl() {
    if (!started && !failed) {
      start();
    }
    if (started && capture != nullptr) {
      capture_audio();
      analyse();
    }
    return bands;
  }

  std::string state_impl() const {
    if (failed) {
      return error.empty() ? "failed" : error;
    }
    return started ? "started" : "idle";
  }
};

Spectrum::Spectrum() : impl_(new Impl()) {}

Spectrum::~Spectrum() { delete impl_; }

std::vector<float> Spectrum::snapshot() {
  if (impl_ == nullptr) {
    return std::vector<float>(kBandCount, 0.0f);
  }
  return impl_->snapshot_impl();
}

std::string Spectrum::state() const {
  if (impl_ == nullptr) {
    return "no impl";
  }
  return impl_->state_impl();
}

}  // namespace ncm
