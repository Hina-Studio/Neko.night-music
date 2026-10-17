#include "ncm/musicjx.hpp"

#include <stdexcept>

#include "ncm/crypto.hpp"

namespace ncm {

const char* MusicJxClient::base_url() {
    return "https://musicjx.com/";
}

MusicJxClient::MusicJxClient() {
    http_.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    http_.set_timeout(timeout_seconds_);
}

void MusicJxClient::set_timeout(int seconds) {
    timeout_seconds_ = seconds > 0 ? seconds : 60;
    http_.set_timeout(timeout_seconds_);
}

std::string MusicJxClient::search(const std::string& input,
                                  const std::string& filter,
                                  const std::string& type, int page) {
    std::string body = "input=" + url_encode(input) +
                       "&filter=" + url_encode(filter) +
                       "&type=" + url_encode(type) +
                       "&page=" + std::to_string(page);
    const Http::Headers headers = {
        {"Content-Type", "application/x-www-form-urlencoded; charset=UTF-8"},
        {"X-Requested-With", "XMLHttpRequest"},
        {"Accept", "application/json, text/javascript, */*; q=0.01"},
        {"Referer", base_url()},
    };
    const HttpResponse response =
        http_.request("POST", base_url(), headers, body);
    if (response.status < 200 || response.status >= 300) {
        if (!response.body.empty() && response.body.front() == '{') {
            return response.body;
        }
        throw std::runtime_error("musicjx http " +
                                 std::to_string(response.status));
    }
    return response.body;
}

}
