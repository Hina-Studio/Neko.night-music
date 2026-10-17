#include "ncm/http.hpp"

#include <curl/curl.h>

#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>

namespace ncm {
namespace {

struct CurlGlobal {
  CurlGlobal() { curl_global_init(CURL_GLOBAL_DEFAULT); }
  ~CurlGlobal() { curl_global_cleanup(); }
};

void ensure_curl() {
  static CurlGlobal global;
  (void)global;
}

std::string trim(const std::string& value) {
  const std::size_t begin = value.find_first_not_of(" \t\r\n");
  if (begin == std::string::npos) {
    return {};
  }
  const std::size_t end = value.find_last_not_of(" \t\r\n");
  return value.substr(begin, end - begin + 1);
}

std::string lower(std::string value) {
  for (char& c : value) {
    if (c >= 'A' && c <= 'Z') {
      c = static_cast<char>(c - 'A' + 'a');
    }
  }
  return value;
}

long parse_status_line(const std::string& line) {
  const std::size_t space = line.find(' ');
  if (space == std::string::npos) {
    return 0;
  }
  try {
    return std::stol(line.substr(space + 1, 3));
  } catch (...) {
    return 0;
  }
}

struct HeaderState {
  HttpResponse* response = nullptr;
  long status = 0;
  std::uint64_t content_length = 0;
  bool has_length = false;
};

std::size_t header_callback(char* buffer, std::size_t size,
                            std::size_t count, void* userdata) {
  auto* state = static_cast<HeaderState*>(userdata);
  const std::size_t length = size * count;
  std::string line(buffer, length);
  while (!line.empty() &&
         (line.back() == '\r' || line.back() == '\n')) {
    line.pop_back();
  }
  if (line.rfind("HTTP/", 0) == 0) {
    state->response->headers.clear();
    state->status = parse_status_line(line);
    state->content_length = 0;
    state->has_length = false;
    return length;
  }
  if (line.empty()) {
    return length;
  }
  const std::size_t colon = line.find(':');
  if (colon == std::string::npos) {
    return length;
  }
  const std::string name = lower(trim(line.substr(0, colon)));
  const std::string value = trim(line.substr(colon + 1));
  state->response->headers.emplace_back(name, value);
  if (name == "content-length") {
    try {
      state->content_length = std::stoull(value);
      state->has_length = true;
    } catch (...) {
      state->content_length = 0;
      state->has_length = false;
    }
  }
  return length;
}

std::size_t write_string_callback(char* ptr, std::size_t size,
                                  std::size_t nmemb, void* userdata) {
  auto* body = static_cast<std::string*>(userdata);
  body->append(ptr, size * nmemb);
  return size * nmemb;
}

struct DownloadState {
  std::string path;
  std::string error;
  std::FILE* file = nullptr;
  long status = 0;
  std::uint64_t existing = 0;
  std::uint64_t content_length = 0;
  bool has_length = false;
  std::uint64_t received = 0;
  bool aborted = false;
  const Http::ProgressFn* progress = nullptr;
};

std::size_t header_download_callback(char* buffer, std::size_t size,
                                     std::size_t count, void* userdata) {
  auto* state = static_cast<DownloadState*>(userdata);
  const std::size_t length = size * count;
  std::string line(buffer, length);
  while (!line.empty() &&
         (line.back() == '\r' || line.back() == '\n')) {
    line.pop_back();
  }
  if (line.rfind("HTTP/", 0) == 0) {
    state->status = parse_status_line(line);
    state->content_length = 0;
    state->has_length = false;
    return length;
  }
  if (line.empty()) {
    return length;
  }
  const std::size_t colon = line.find(':');
  if (colon == std::string::npos) {
    return length;
  }
  const std::string name = lower(trim(line.substr(0, colon)));
  const std::string value = trim(line.substr(colon + 1));
  if (name == "content-length") {
    try {
      state->content_length = std::stoull(value);
      state->has_length = true;
    } catch (...) {
      state->content_length = 0;
      state->has_length = false;
    }
  }
  return length;
}

std::size_t write_download_callback(char* ptr, std::size_t size,
                                    std::size_t nmemb, void* userdata) {
  auto* state = static_cast<DownloadState*>(userdata);
  const std::size_t length = size * nmemb;
  if (state->file == nullptr) {
    if (state->status != 200 && state->status != 206) {
      state->error = "download failed with HTTP status " +
                     std::to_string(state->status);
      return 0;
    }
    const char* mode = state->status == 206 ? "ab" : "wb";
    state->file = std::fopen(state->path.c_str(), mode);
    if (state->file == nullptr) {
      state->error = "cannot open output file: " + state->path;
      return 0;
    }
    state->existing = state->status == 206 ? state->existing : 0;
    state->received = state->existing;
    if (state->progress) {
      const std::uint64_t total =
          state->has_length ? state->existing + state->content_length : 0;
      if (!(*state->progress)(state->received, total)) {
        state->aborted = true;
        state->error = "download aborted";
        return 0;
      }
    }
  }
  if (std::fwrite(ptr, 1, length, state->file) != length) {
    state->error = "failed to write output file: " + state->path;
    return 0;
  }
  state->received += length;
  if (state->progress) {
    const std::uint64_t total =
        state->has_length ? state->existing + state->content_length : 0;
    if (!(*state->progress)(state->received, total)) {
      state->aborted = true;
      state->error = "download aborted";
      return 0;
    }
  }
  return length;
}

curl_slist* build_headers(const Http::Headers& headers) {
  curl_slist* list = nullptr;
  for (const auto& header : headers) {
    if (header.first.empty() || header.second.empty()) {
      continue;
    }
    list = curl_slist_append(list, (header.first + ": " + header.second).c_str());
  }
  return list;
}

void apply_common(CURL* curl, const std::string& user_agent,
                  int timeout_seconds) {
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(curl, CURLOPT_MAXREDIRS, 10L);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, static_cast<long>(timeout_seconds));
  curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "");
  curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(
      curl, CURLOPT_USERAGENT,
      user_agent.empty() ? "ncm_core/1.0" : user_agent.c_str());
}

}  // namespace

Http::Http() {
  ensure_curl();
  session_ = curl_easy_init();
  if (session_ == nullptr) {
    throw std::runtime_error("curl_easy_init failed");
  }
}

Http::~Http() {
  if (session_ != nullptr) {
    curl_easy_cleanup(static_cast<CURL*>(session_));
  }
}

void Http::set_timeout(int seconds) {
  timeout_seconds_ = seconds > 0 ? seconds : 30;
}

void Http::set_user_agent(const std::string& ua) {
  user_agent_ = ua;
}

void Http::apply_timeouts() {}

HttpResponse Http::request(const std::string& method, const std::string& url,
                           const Headers& headers, const std::string& body) {
  CURL* curl = static_cast<CURL*>(session_);
  curl_easy_reset(curl);
  apply_common(curl, user_agent_, timeout_seconds_);

  HttpResponse response;
  HeaderState header_state;
  header_state.response = &response;
  std::string response_body;

  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, header_callback);
  curl_easy_setopt(curl, CURLOPT_HEADERDATA, &header_state);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_string_callback);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);

  curl_slist* header_list = build_headers(headers);
  if (header_list != nullptr) {
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
  }

  if (method == "POST") {
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.data());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE,
                     static_cast<curl_off_t>(body.size()));
  } else if (method == "GET") {
    curl_easy_setopt(curl, CURLOPT_HTTPGET, 1L);
  } else {
    curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, method.c_str());
    if (!body.empty()) {
      curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.data());
      curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE,
                       static_cast<curl_off_t>(body.size()));
    }
  }

  const CURLcode result = curl_easy_perform(curl);
  if (header_list != nullptr) {
    curl_slist_free_all(header_list);
  }
  if (result != CURLE_OK) {
    throw std::runtime_error(curl_easy_strerror(result));
  }

  long status = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  char* effective_url = nullptr;
  curl_easy_getinfo(curl, CURLINFO_EFFECTIVE_URL, &effective_url);
  response.status = static_cast<int>(status);
  if (effective_url != nullptr) {
    response.final_url = effective_url;
  }
  response.body = std::move(response_body);
  return response;
}

bool Http::download(const std::string& url, const std::string& file_path,
                    const Headers& headers, const ProgressFn& progress,
                    std::string& error, bool resume) {
  DownloadState state;
  state.path = file_path;
  state.progress = &progress;
  try {
    if (resume) {
      std::error_code size_error;
      if (std::filesystem::is_regular_file(file_path, size_error)) {
        state.existing = static_cast<std::uint64_t>(
            std::filesystem::file_size(file_path, size_error));
      }
    }

    CURL* curl = static_cast<CURL*>(session_);
    curl_easy_reset(curl);
    apply_common(curl, user_agent_, timeout_seconds_);
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPGET, 1L);
    curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, header_download_callback);
    curl_easy_setopt(curl, CURLOPT_HEADERDATA, &state);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_download_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &state);

    Headers ranged = headers;
    if (state.existing > 0) {
      ranged.emplace_back("Range",
                          "bytes=" + std::to_string(state.existing) + "-");
    }
    curl_slist* header_list = build_headers(ranged);
    if (header_list != nullptr) {
      curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    }

    const CURLcode result = curl_easy_perform(curl);
    if (header_list != nullptr) {
      curl_slist_free_all(header_list);
    }
    if (state.file != nullptr) {
      std::fclose(state.file);
      state.file = nullptr;
    }

    if (result == CURLE_WRITE_ERROR && !state.error.empty()) {
      error = state.error;
      return false;
    }
    if (result != CURLE_OK) {
      error = curl_easy_strerror(result);
      return false;
    }
    if (state.status != 200 && state.status != 206) {
      error = "download failed with HTTP status " +
              std::to_string(state.status);
      return false;
    }
    if (state.file == nullptr) {
      std::FILE* empty = std::fopen(file_path.c_str(), "wb");
      if (empty == nullptr) {
        error = "cannot open output file: " + file_path;
        return false;
      }
      std::fclose(empty);
    }
    (void)progress;
    return true;
  } catch (const std::exception& ex) {
    if (state.file != nullptr) {
      std::fclose(state.file);
      state.file = nullptr;
    }
    error = ex.what();
    return false;
  }
}

std::string Http::resolve_redirect(const std::string& url) {
  try {
    const HttpResponse response = request("GET", url, {}, {});
    if (!response.final_url.empty()) {
      return response.final_url;
    }
  } catch (const std::exception&) {
  }
  return {};
}

}  // namespace ncm
