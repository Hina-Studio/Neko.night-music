#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace ncm {

struct HttpResponse {
    int status = 0;
    std::string body;
    std::string final_url;
    std::vector<std::pair<std::string, std::string>> headers;
};

class Http {
public:
    using Headers = std::vector<std::pair<std::string, std::string>>;
    using ProgressFn = std::function<bool(std::uint64_t received, std::uint64_t total)>;

    Http();
    ~Http();

    Http(const Http&) = delete;
    Http& operator=(const Http&) = delete;

    void set_timeout(int seconds);
    void set_user_agent(const std::string& ua);

    HttpResponse request(const std::string& method,
                         const std::string& url,
                         const Headers& headers,
                         const std::string& body);

    bool download(const std::string& url,
                  const std::string& file_path,
                  const Headers& headers,
                  const ProgressFn& progress,
                  std::string& error,
                  bool resume = false);

    std::string resolve_redirect(const std::string& url);

private:
    void* session_ = nullptr;
    int timeout_seconds_ = 30;
    std::string user_agent_;

    void apply_timeouts();
};

}
