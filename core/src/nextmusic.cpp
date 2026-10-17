#include "ncm/nextmusic.hpp"

#include <nlohmann/json.hpp>

#include <chrono>
#include <stdexcept>

namespace ncm {
namespace {

using json = nlohmann::json;

std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

json parse_json(const std::string& text) {
    try {
        return json::parse(text);
    } catch (const json::exception& ex) {
        throw std::runtime_error(std::string("invalid json from nextmusic: ") +
                                 ex.what());
    }
}

}

const char* NextMusicClient::base_url() {
    return "https://nextmusic.toubiec.cn";
}

NextMusicClient::NextMusicClient() {
    http_.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
    http_.set_timeout(timeout_seconds_);
}

void NextMusicClient::set_timeout(int seconds) {
    timeout_seconds_ = seconds > 0 ? seconds : 30;
    http_.set_timeout(timeout_seconds_);
}

std::string NextMusicClient::get_ip(bool refresh) {
    if (!refresh && !cached_ip_.empty()) {
        return cached_ip_;
    }
    try {
        json payload;
        payload["timestamp"] = now_ms();
        const HttpResponse response =
            http_.request("POST", std::string(base_url()) + "/api/ip",
                          {{"Content-Type", "application/json"}}, payload.dump());
        const json body = parse_json(response.body);
        if (body.contains("data") && body["data"].is_object()) {
            const json& data = body["data"];
            if (data.contains("ip") && data["ip"].is_string()) {
                cached_ip_ = data["ip"].get<std::string>();
            }
        }
    } catch (const std::exception&) {
        cached_ip_.clear();
    }
    return cached_ip_;
}

std::string NextMusicClient::request(const std::string& path,
                                     const std::string& payload_json) {
    json payload = payload_json.empty() ? json::object() : parse_json(payload_json);
    if (!payload.is_object()) {
        payload = json::object();
    }
    if (!payload.contains("timestamp")) {
        payload["timestamp"] = now_ms();
    }
    if (!payload.contains("ip")) {
        const std::string ip = get_ip(false);
        if (!ip.empty()) {
            payload["ip"] = ip;
        }
    }

    const std::string url = std::string(base_url()) + path;
    const HttpResponse response =
        http_.request("POST", url, {{"Content-Type", "application/json"}},
                      payload.dump());
    if (response.status < 200 || response.status >= 300) {
        if (!response.body.empty() && response.body.front() == '{') {
            return response.body;
        }
        throw std::runtime_error("nextmusic http " +
                                 std::to_string(response.status) + " for " + path);
    }
    return response.body;
}

std::string NextMusicClient::search(const std::string& keyword, int limit,
                                    int offset) {
    json payload;
    payload["keyword"] = keyword;
    payload["type"] = 1;
    payload["limit"] = limit;
    payload["offset"] = offset;
    return request("/api/search", payload.dump());
}

std::string NextMusicClient::song_info(const std::string& id) {
    json payload;
    payload["id"] = id;
    return request("/api/getSongInfo", payload.dump());
}

std::string NextMusicClient::song_url(const std::string& id,
                                      const std::string& level,
                                      const std::string& immerse_type) {
    json payload;
    payload["id"] = id;
    payload["level"] = level;
    if (level == "sky" && !immerse_type.empty()) {
        payload["immerseType"] = immerse_type;
    }
    return request("/api/getSongUrl", payload.dump());
}

std::string NextMusicClient::song_lyric(const std::string& id) {
    json payload;
    payload["id"] = id;
    return request("/api/getSongLyric", payload.dump());
}

std::string NextMusicClient::album(const std::string& id) {
    json payload;
    payload["id"] = id;
    return request("/api/getAlbum", payload.dump());
}

std::string NextMusicClient::playlist_trackall(const std::string& id) {
    json payload;
    payload["id"] = id;
    return request("/api/playlist_trackall", payload.dump());
}

std::string NextMusicClient::artist_songs(const std::string& id, int limit,
                                          int offset) {
    json payload;
    payload["id"] = id;
    payload["order"] = "hot";
    payload["workType"] = 1;
    payload["withArtistDetail"] = true;
    payload["limit"] = limit;
    payload["offset"] = offset;
    return request("/api/artist_songs", payload.dump());
}

std::string NextMusicClient::toplist() {
    return request("/api/toplist", "{}");
}

}
