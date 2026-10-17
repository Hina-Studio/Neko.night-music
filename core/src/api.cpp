#include "ncm/api.hpp"

#ifdef _WIN32
#include <windows.h>
#endif

#include <nlohmann/json.hpp>

#include "ncm/crypto.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <random>
#include <stdexcept>

namespace ncm {
namespace {

using json = nlohmann::json;

const char* kUserAgent =
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/2.10.2.200154";
const char* kReferer = "https://music.163.com/";

const char* kSongUrlV1 =
    "https://interface3.music.163.com/eapi/song/enhance/player/url/v1";
const char* kSongDetailV3 = "https://interface3.music.163.com/api/v3/song/detail";
const char* kLyricApi = "https://interface3.music.163.com/api/song/lyric";
const char* kSearchApi = "https://music.163.com/api/cloudsearch/pc";
const char* kPlaylistDetailApi = "https://music.163.com/api/v6/playlist/detail";
const char* kAlbumDetailApi = "https://music.163.com/api/v1/album/";
const char* kQrUnikeyApi =
    "https://interface3.music.163.com/eapi/login/qrcode/unikey";
const char* kQrLoginApi =
    "https://interface3.music.163.com/eapi/login/qrcode/client/login";
const char* kLoginApi = "https://interface3.music.163.com/eapi/login";
const char* kRecommendApi =
    "https://music.163.com/api/v1/discovery/recommend/songs";
const char* kFmApi = "https://music.163.com/api/radio/get";
const char* kToplistApi = "https://music.163.com/api/toplist";
const char* kLikeGetApi = "https://music.163.com/api/song/like/get";
const char* kLikeApi = "https://music.163.com/api/radio/like";
const char* kUserPlaylistApi = "https://music.163.com/api/user/playlist";
const char* kCommentsApi =
    "https://music.163.com/api/v1/resource/comments/R_SO_4_";
const char* kArtistApi = "https://music.163.com/api/v1/artist/";
const char* kArtistSongsApi = "https://music.163.com/api/v1/artist/songs";
const char* kArtistAlbumsApi = "https://music.163.com/api/artist/albums/";
const char* kNewSongsApi = "https://music.163.com/api/v1/discovery/new/songs";

std::filesystem::path to_path(const std::string& value) {
#ifdef _WIN32
    if (value.empty()) {
        return {};
    }
    const int len = MultiByteToWideChar(CP_UTF8, 0, value.c_str(),
                                        static_cast<int>(value.size()), nullptr, 0);
    if (len <= 0) {
        return {};
    }
    std::wstring out(static_cast<std::size_t>(len), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()),
                        out.data(), len);
    return std::filesystem::path(out);
#else
    return std::filesystem::path(value);
#endif
}

std::string trim(const std::string& value) {
    const std::size_t begin = value.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return {};
    }
    const std::size_t end = value.find_last_not_of(" \t\r\n");
    return value.substr(begin, end - begin + 1);
}

std::string random_request_id() {
    static std::mt19937 rng(std::random_device{}());
    std::uniform_int_distribution<int> dist(20000000, 29999999);
    return std::to_string(dist(rng));
}

std::string header_config() {
    json config = {
        {"os", "pc"},     {"appver", ""}, {"osver", ""},
        {"deviceId", "pyncm!"}, {"requestId", random_request_id()},
    };
    return config.dump();
}

json parse_json(const std::string& body) {
    try {
        return json::parse(body);
    } catch (const json::exception& ex) {
        throw std::runtime_error(std::string("invalid json response: ") + ex.what());
    }
}

std::string json_string(const json& node, const char* key,
                        const std::string& fallback = {}) {
    if (node.is_object() && node.contains(key) && node[key].is_string()) {
        return node[key].get<std::string>();
    }
    return fallback;
}

std::int64_t json_int(const json& node, const char* key,
                      std::int64_t fallback = 0) {
    if (node.is_object() && node.contains(key)) {
        const json& value = node[key];
        if (value.is_number_integer()) {
            return value.get<std::int64_t>();
        }
        if (value.is_number()) {
            return static_cast<std::int64_t>(value.get<double>());
        }
    }
    return fallback;
}

json json_object(const json& node, const char* key) {
    if (node.is_object() && node.contains(key) && node[key].is_object()) {
        return node[key];
    }
    return json::object();
}

json json_array(const json& node, const char* key) {
    if (node.is_object() && node.contains(key) && node[key].is_array()) {
        return node[key];
    }
    return json::array();
}

bool json_bool(const json& node, const char* key, bool fallback = false) {
    if (node.is_object() && node.contains(key) && node[key].is_boolean()) {
        return node[key].get<bool>();
    }
    return fallback;
}

std::string artists_join(const json& artists) {
    if (!artists.is_array()) {
        return {};
    }
    std::string out;
    for (const json& artist : artists) {
        if (!artist.is_object()) {
            continue;
        }
        if (!out.empty()) {
            out += '/';
        }
        out += json_string(artist, "name");
    }
    return out;
}

std::string album_pic_url(const json& album) {
    if (!album.is_object()) {
        return {};
    }
    const std::string pic = json_string(album, "picUrl");
    if (!pic.empty()) {
        return pic;
    }
    if (album.contains("pic") && album["pic"].is_number()) {
        return pic_url(album["pic"].get<std::int64_t>(), 300);
    }
    if (album.contains("picId") && album["picId"].is_number()) {
        return pic_url(album["picId"].get<std::int64_t>(), 300);
    }
    return {};
}

json song_brief(const json& song) {
    json item;
    item["id"] = json_int(song, "id", 0);
    item["name"] = json_string(song, "name");
    json artists = json_array(song, "ar");
    if (artists.empty()) {
        artists = json_array(song, "artists");
    }
    item["artists"] = artists_join(artists);
    json artist_ids = json::array();
    for (const json& artist : artists) {
        const std::int64_t artist_id = json_int(artist, "id", 0);
        if (artist_id > 0) {
            artist_ids.push_back(artist_id);
        }
    }
    item["artist_ids"] = artist_ids;
    json album = json_object(song, "al");
    if (album.empty()) {
        album = json_object(song, "album");
    }
    if (album.empty()) {
        album = json_object(song, "albumData");
    }
    item["album"] = json_string(album, "name");
    item["picUrl"] = album_pic_url(album);
    return item;
}

json album_brief(const json& album) {
    json item;
    item["id"] = json_int(album, "id", 0);
    item["name"] = json_string(album, "name");
    item["picUrl"] = album_pic_url(album);
    item["size"] = json_int(album, "size", 0);
    item["publishTime"] = json_int(album, "publishTime", 0);
    item["artist"] = json_string(json_object(album, "artist"), "name");
    if (item["artist"].get<std::string>().empty()) {
        item["artist"] = artists_join(json_array(album, "artists"));
    }
    return item;
}

json comment_brief(const json& comment) {
    json item;
    item["id"] = json_int(comment, "commentId", 0);
    item["content"] = json_string(comment, "content");
    item["liked_count"] = json_int(comment, "likedCount", 0);
    item["time"] = json_int(comment, "time", 0);
    const json user = json_object(comment, "user");
    item["nickname"] = json_string(user, "nickname");
    item["avatar"] = json_string(user, "avatarUrl");
    return item;
}

std::string format_size(std::int64_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    double size = static_cast<double>(bytes);
    int unit = 0;
    while (size >= 1024.0 && unit < 4) {
        size /= 1024.0;
        ++unit;
    }
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.2f%s", size, units[unit]);
    return buffer;
}

std::string sanitize_filename(const std::string& name) {
    std::string out;
    out.reserve(name.size());
    for (char c : name) {
        if (std::strchr("<>:\"/\\|?*", c) != nullptr ||
            static_cast<unsigned char>(c) < 0x20) {
            continue;
        }
        out.push_back(c);
    }
    while (!out.empty() && (out.back() == ' ' || out.back() == '.')) {
        out.pop_back();
    }
    return out.empty() ? "music" : out;
}

std::int64_t to_song_id(const std::string& id) {
    const std::string text = trim(id);
    try {
        std::size_t consumed = 0;
        const long long value = std::stoll(text, &consumed);
        if (consumed == 0) {
            throw std::invalid_argument("empty");
        }
        return value;
    } catch (const std::exception&) {
        throw std::runtime_error("invalid song id: " + text);
    }
}

}

Client::Client() {
    http_.set_user_agent(kUserAgent);
}

void Client::set_cookie(const std::string& cookie) {
    cookie_ = trim(cookie);
}

std::string Client::cookie() const {
    return cookie_;
}

void Client::set_timeout(int seconds) {
    http_.set_timeout(seconds);
}

Http::Headers Client::base_headers() const {
    Http::Headers headers;
    headers.emplace_back("Referer", kReferer);
    if (!cookie_.empty()) {
        headers.emplace_back("Cookie", cookie_);
    }
    return headers;
}

std::string Client::post_form(const std::string& url,
                              const std::vector<std::pair<std::string, std::string>>& fields,
                              bool with_headers) {
    std::string body;
    for (const auto& field : fields) {
        if (!body.empty()) {
            body += '&';
        }
        body += url_encode(field.first) + "=" + url_encode(field.second);
    }

    Http::Headers headers;
    headers.emplace_back("Content-Type", "application/x-www-form-urlencoded");
    if (with_headers) {
        const Http::Headers base = base_headers();
        headers.insert(headers.end(), base.begin(), base.end());
    }

    const HttpResponse response = http_.request("POST", url, headers, body);
    if (response.status < 200 || response.status >= 300) {
        throw std::runtime_error("http " + std::to_string(response.status) +
                                 " for " + url);
    }
    return response.body;
}

std::string Client::post_eapi(const std::string& url, const std::string& payload_json) {
    const std::string params = eapi_encrypt_params(url, payload_json);
    return post_form(url, {{"params", params}}, true);
}

std::string Client::get_with_headers(const std::string& url) {
    const HttpResponse response = http_.request("GET", url, base_headers(), {});
    if (response.status < 200 || response.status >= 300) {
        throw std::runtime_error("http " + std::to_string(response.status) +
                                 " for " + url);
    }
    return response.body;
}

std::string Client::search(const std::string& keywords, int limit, int offset) {
    const std::string body = post_form(
        kSearchApi,
        {{"s", keywords},
         {"type", "1"},
         {"limit", std::to_string(limit)},
         {"offset", std::to_string(offset)}},
        true);
    const json result = parse_json(body);
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("search failed: " +
                                 json_string(result, "message", "unknown error"));
    }

    json out;
    out["code"] = 200;
    out["songs"] = json::array();
    const json result_node = json_object(result, "result");
    const json songs = json_array(result_node, "songs");
    for (const json& song : songs) {
        out["songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::song_detail(const std::string& id) {
    json query = json::array();
    query.push_back({{"id", to_song_id(id)}, {"v", 0}});
    const std::string body =
        post_form(kSongDetailV3, {{"c", query.dump()}}, false);
    const json result = parse_json(body);
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("song detail failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    return result.dump();
}

std::string Client::song_url(const std::string& id, const std::string& level) {
    json payload;
    payload["ids"] = json::array({to_song_id(id)});
    payload["level"] = level;
    payload["encodeType"] = level == "dolby" ? "mp4" : "flac";
    payload["header"] = header_config();
    if (level == "sky") {
        payload["immerseType"] = "c51";
    }

    const json result = parse_json(post_eapi(kSongUrlV1, payload.dump()));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("song url failed: " +
                                 json_string(result, "message", "unknown error"));
    }

    json out;
    out["code"] = 200;
    out["id"] = to_song_id(id);
    out["url"] = "";
    out["level"] = level;
    out["size"] = 0;
    out["type"] = "";
    out["br"] = 0;
    if (result.contains("data") && result["data"].is_array() &&
        !result["data"].empty() && result["data"][0].is_object()) {
        const json& data = result["data"][0];
        out["id"] = json_int(data, "id", to_song_id(id));
        out["url"] = json_string(data, "url");
        out["level"] = json_string(data, "level", level);
        out["size"] = json_int(data, "size", 0);
        out["type"] = json_string(data, "type");
        out["br"] = json_int(data, "br", 0);
    }
    return out.dump();
}

std::string Client::song_full(const std::string& id, const std::string& level) {
    const std::string resolved = resolve_id(id);
    const json detail = parse_json(song_detail(resolved));
    if (!detail.contains("songs") || !detail["songs"].is_array() ||
        detail["songs"].empty()) {
        throw std::runtime_error("song not found: " + resolved);
    }

    const json song = detail["songs"][0];
    const json url_info = parse_json(song_url(resolved, level));
    const json lyric_info = parse_json(lyric(resolved));

    json out;
    out["id"] = resolved;
    out["name"] = json_string(song, "name");
    out["ar_name"] = artists_join(json_array(song, "ar"));
    const json album = json_object(song, "al");
    out["al_name"] = json_string(album, "name");
    out["pic"] = album_pic_url(album);
    out["duration"] = json_int(song, "dt", 0);
    out["level"] = json_string(url_info, "level", level);
    out["url"] = json_string(url_info, "url");
    out["type"] = json_string(url_info, "type");
    out["bitrate"] = json_int(url_info, "br", 0);
    out["size"] = json_int(url_info, "size", 0);
    out["size_formatted"] = format_size(json_int(url_info, "size", 0));
    out["lyric"] = json_string(lyric_info, "lrc");
    out["tlyric"] = json_string(lyric_info, "tlyric");
    return out.dump();
}

std::string Client::lyric(const std::string& id) {
    const std::string body = post_form(
        kLyricApi,
        {{"id", std::to_string(to_song_id(id))},
         {"cp", "false"},
         {"tv", "0"},
         {"lv", "0"},
         {"rv", "0"},
         {"kv", "0"},
         {"yv", "0"},
         {"ytv", "0"},
         {"yrv", "0"}},
        true);
    const json result = parse_json(body);
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("lyric failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["lrc"] = json_string(json_object(result, "lrc"), "lyric");
    out["tlyric"] = json_string(json_object(result, "tlyric"), "lyric");
    return out.dump();
}

std::string Client::playlist(const std::string& id) {
    const json result = parse_json(
        post_form(kPlaylistDetailApi, {{"id", trim(id)}}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("playlist failed: " +
                                 json_string(result, "message", "unknown error"));
    }

    const json node = json_object(result, "playlist");
    json info;
    info["id"] = json_int(node, "id", 0);
    info["name"] = json_string(node, "name");
    info["coverImgUrl"] = json_string(node, "coverImgUrl");
    info["creator"] = json_string(json_object(node, "creator"), "nickname");
    info["trackCount"] = json_int(node, "trackCount", 0);
    info["description"] = json_string(node, "description");
    info["tracks"] = json::array();

    std::vector<std::int64_t> track_ids;
    const json track_ids_node = json_array(node, "trackIds");
    for (const json& track : track_ids_node) {
        const std::int64_t track_id = json_int(track, "id", 0);
        if (track_id > 0) {
            track_ids.push_back(track_id);
        }
    }

    const std::size_t batch_size = 100;
    for (std::size_t offset = 0; offset < track_ids.size(); offset += batch_size) {
        json query = json::array();
        const std::size_t end = std::min(offset + batch_size, track_ids.size());
        for (std::size_t i = offset; i < end; ++i) {
            query.push_back({{"id", track_ids[i]}, {"v", 0}});
        }
        const json batch = parse_json(
            post_form(kSongDetailV3, {{"c", query.dump()}}, true));
        const json batch_songs = json_array(batch, "songs");
        for (const json& song : batch_songs) {
            info["tracks"].push_back(song_brief(song));
        }
    }
    return info.dump();
}

std::string Client::album(const std::string& id) {
    const json result =
        parse_json(get_with_headers(std::string(kAlbumDetailApi) + trim(id)));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("album failed: " +
                                 json_string(result, "message", "unknown error"));
    }

    const json node = json_object(result, "album");
    json info;
    info["id"] = json_int(node, "id", 0);
    info["name"] = json_string(node, "name");
    info["coverImgUrl"] = album_pic_url(node);
    info["artist"] = json_string(json_object(node, "artist"), "name");
    info["publishTime"] = json_int(node, "publishTime", 0);
    info["description"] = json_string(node, "description");
    info["songs"] = json::array();
    const json songs = json_array(result, "songs");
    for (const json& song : songs) {
        info["songs"].push_back(song_brief(song));
    }
    return info.dump();
}

std::string Client::qr_unikey() {
    json payload;
    payload["type"] = 1;
    payload["header"] = header_config();
    const json result = parse_json(post_eapi(kQrUnikeyApi, payload.dump()));

    json out;
    out["code"] = json_int(result, "code", -1);
    out["unikey"] = json_string(result, "unikey");
    out["message"] = json_string(result, "message");
    return out.dump();
}

std::string Client::qr_check(const std::string& unikey) {
    json payload;
    payload["key"] = unikey;
    payload["type"] = 1;
    payload["header"] = header_config();

    const std::string params = eapi_encrypt_params(kQrLoginApi, payload.dump());
    const HttpResponse response =
        http_.request("POST", kQrLoginApi,
                      [&] {
                          Http::Headers headers = base_headers();
                          headers.insert(headers.begin(),
                                         {"Content-Type",
                                          "application/x-www-form-urlencoded"});
                          return headers;
                      }(),
                      "params=" + params);

    const json result = parse_json(response.body);
    json out;
    out["code"] = json_int(result, "code", -1);
    out["message"] = json_string(result, "message");
    out["music_u"] = "";
    if (out["code"].get<int>() == 803) {
        for (const auto& header : response.headers) {
            if (header.first != "set-cookie") {
                continue;
            }
            const std::size_t pos = header.second.find("MUSIC_U=");
            if (pos == std::string::npos) {
                continue;
            }
            std::string value = header.second.substr(pos + 8);
            const std::size_t end = value.find(';');
            if (end != std::string::npos) {
                value = value.substr(0, end);
            }
            out["music_u"] = trim(value);
            break;
        }
    }
    return out.dump();
}

std::string Client::login_password(const std::string& username,
                                   const std::string& password) {
    json config = {
        {"os", "pc"},
        {"appver", "8.9.70"},
        {"osver", "10"},
        {"deviceId", "pyncm!"},
        {"requestId", random_request_id()},
    };

    json payload;
    payload["username"] = username;
    payload["password"] = md5_hex(password);
    payload["rememberLogin"] = "true";
    payload["csrf_token"] = "";
    payload["header"] = config.dump();

    const std::string params = eapi_encrypt_params(kLoginApi, payload.dump());
    Http::Headers headers = base_headers();
    headers.insert(headers.begin(),
                   {"Content-Type", "application/x-www-form-urlencoded"});
    headers.insert(headers.begin(),
                   {"Cookie", "os=pc;appver=8.9.70;osver=10;deviceId=pyncm!"});
    const HttpResponse response =
        http_.request("POST", kLoginApi, headers, "params=" + params);

    const json result = parse_json(response.body);
    json out;
    out["code"] = json_int(result, "code", -1);
    out["message"] = json_string(result, "message");
    out["music_u"] = "";
    out["cookie"] = "";
    out["nickname"] = "";
    out["user_id"] = 0;

    for (const auto& header : response.headers) {
        if (header.first != "set-cookie") {
            continue;
        }
        const std::size_t pos = header.second.find("MUSIC_U=");
        if (pos == std::string::npos) {
            continue;
        }
        std::string value = header.second.substr(pos + 8);
        const std::size_t end = value.find(';');
        if (end != std::string::npos) {
            value = value.substr(0, end);
        }
        out["music_u"] = trim(value);
        break;
    }

    if (result.contains("account") && result["account"].is_object()) {
        out["user_id"] = json_int(result["account"], "id", 0);
    }
    if (result.contains("profile") && result["profile"].is_object()) {
        out["nickname"] = json_string(result["profile"], "nickname");
        if (out["user_id"].get<std::int64_t>() == 0) {
            out["user_id"] = json_int(result["profile"], "userId", 0);
        }
    }
    if (!out["music_u"].get<std::string>().empty()) {
        out["cookie"] =
            "MUSIC_U=" + out["music_u"].get<std::string>() + ";os=pc;appver=8.9.70;";
    }
    return out.dump();
}

std::string Client::user_account() {
    const HttpResponse response = http_.request(
        "GET", "https://music.163.com/api/nuser/account/get", base_headers(), {});
    const json result = parse_json(response.body);
    const json profile = json_object(result, "profile");
    const json account = json_object(result, "account");

    json out;
    out["code"] = json_int(result, "code", response.status);
    out["message"] = json_string(result, "message");
    out["user_id"] = json_int(profile, "userId", json_int(account, "id", 0));
    out["nickname"] = json_string(profile, "nickname");
    out["avatar"] = json_string(profile, "avatarUrl");
    out["vip_type"] = json_int(profile, "vipType", 0);
    return out.dump();
}

std::string Client::recommend_songs() {
    const json result =
        parse_json(post_form(kRecommendApi, {{"csrf_token", ""}}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("recommend failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json songs = json_array(result, "recommend");
    if (songs.empty()) {
        songs = json_array(json_object(result, "data"), "dailySongs");
    }
    json out;
    out["code"] = 200;
    out["songs"] = json::array();
    for (const json& song : songs) {
        out["songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::personal_fm() {
    const json result =
        parse_json(post_form(kFmApi, {{"csrf_token", ""}}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("fm failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["songs"] = json::array();
    for (const json& song : json_array(result, "data")) {
        out["songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::toplists() {
    const json result = parse_json(post_form(kToplistApi, {}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("toplist failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["lists"] = json::array();
    for (const json& item : json_array(result, "list")) {
        json entry;
        entry["id"] = json_int(item, "id", 0);
        entry["name"] = json_string(item, "name");
        entry["cover"] = json_string(item, "coverImgUrl");
        if (entry["cover"].get<std::string>().empty()) {
            entry["cover"] = json_string(item, "coverImageUrl");
        }
        entry["update_frequency"] = json_string(item, "updateFrequency");
        entry["description"] = json_string(item, "description");
        out["lists"].push_back(entry);
    }
    return out.dump();
}

std::string Client::liked_song_ids() {
    const json result = parse_json(
        post_form(kLikeGetApi, {{"trackIds", "[]"}, {"csrf_token", ""}}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("liked ids failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["ids"] = json_array(result, "ids");
    return out.dump();
}

std::string Client::like_song(const std::string& id, bool like) {
    const json result = parse_json(post_form(
        kLikeApi,
        {{"alg", "itembased"},
         {"trackId", trim(id)},
         {"like", like ? "true" : "false"},
         {"time", "3"},
         {"csrf_token", ""}},
        true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("like failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["like"] = like;
    out["playlist_id"] = json_int(result, "playlistId", 0);
    return out.dump();
}

std::string Client::user_playlists(const std::string& uid, int limit,
                                   int offset) {
    const json result = parse_json(post_form(
        kUserPlaylistApi,
        {{"uid", trim(uid)},
         {"limit", std::to_string(limit)},
         {"offset", std::to_string(offset)},
         {"csrf_token", ""}},
        true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("user playlists failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["playlists"] = json::array();
    for (const json& item : json_array(result, "playlist")) {
        json entry;
        entry["id"] = json_int(item, "id", 0);
        entry["name"] = json_string(item, "name");
        entry["cover"] = json_string(item, "coverImgUrl");
        entry["track_count"] = json_int(item, "trackCount", 0);
        entry["creator"] = json_string(json_object(item, "creator"), "nickname");
        entry["subscribed"] = json_bool(item, "subscribed", false);
        out["playlists"].push_back(entry);
    }
    return out.dump();
}

std::string Client::comments(const std::string& id, int limit, int offset) {
    const json result = parse_json(post_form(
        std::string(kCommentsApi) + trim(id),
        {{"limit", std::to_string(limit)},
         {"offset", std::to_string(offset)},
         {"csrf_token", ""}},
        true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("comments failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["total"] = json_int(result, "total", 0);
    out["hot"] = json::array();
    out["comments"] = json::array();
    for (const json& item : json_array(result, "hotComments")) {
        out["hot"].push_back(comment_brief(item));
    }
    for (const json& item : json_array(result, "comments")) {
        out["comments"].push_back(comment_brief(item));
    }
    return out.dump();
}

std::string Client::artist_detail(const std::string& id) {
    const json result =
        parse_json(get_with_headers(std::string(kArtistApi) + trim(id)));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("artist failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    const json artist = json_object(result, "artist");
    json out;
    out["code"] = 200;
    out["id"] = json_int(artist, "id", 0);
    out["name"] = json_string(artist, "name");
    out["alias"] = json_array(artist, "alias");
    out["picUrl"] = json_string(artist, "picUrl");
    if (out["picUrl"].get<std::string>().empty()) {
        out["picUrl"] = json_string(artist, "img1v1Url");
    }
    out["hot_songs"] = json::array();
    for (const json& song : json_array(result, "hotSongs")) {
        out["hot_songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::artist_songs(const std::string& id, int limit,
                                 int offset) {
    const json result = parse_json(post_form(
        kArtistSongsApi,
        {{"id", trim(id)},
         {"limit", std::to_string(limit)},
         {"offset", std::to_string(offset)},
         {"order", "hot"},
         {"csrf_token", ""}},
        true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("artist songs failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["total"] = json_int(result, "total", 0);
    out["more"] = json_bool(result, "more", false);
    out["songs"] = json::array();
    for (const json& song : json_array(result, "songs")) {
        out["songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::artist_albums(const std::string& id, int limit,
                                  int offset) {
    const json result = parse_json(post_form(
        std::string(kArtistAlbumsApi) + trim(id),
        {{"limit", std::to_string(limit)},
         {"offset", std::to_string(offset)},
         {"csrf_token", ""}},
        true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("artist albums failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["more"] = json_bool(result, "more", false);
    out["albums"] = json::array();
    for (const json& item : json_array(result, "hotAlbums")) {
        out["albums"].push_back(album_brief(item));
    }
    return out.dump();
}

std::string Client::new_songs(int area_id) {
    const json result = parse_json(post_form(
        kNewSongsApi,
        {{"areaId", std::to_string(area_id)}, {"csrf_token", ""}}, true));
    if (json_int(result, "code", 0) != 200) {
        throw std::runtime_error("new songs failed: " +
                                 json_string(result, "message", "unknown error"));
    }
    json out;
    out["code"] = 200;
    out["songs"] = json::array();
    for (const json& song : json_array(result, "data")) {
        out["songs"].push_back(song_brief(song));
    }
    return out.dump();
}

std::string Client::download_song(
    const std::string& id, const std::string& level, const std::string& directory,
    const std::function<bool(std::uint64_t, std::uint64_t)>& progress) {
    const std::string resolved = resolve_id(id);
    const json detail = parse_json(song_detail(resolved));
    if (!detail.contains("songs") || !detail["songs"].is_array() ||
        detail["songs"].empty()) {
        throw std::runtime_error("song not found: " + resolved);
    }
    const json song = detail["songs"][0];
    const json url_info = parse_json(song_url(resolved, level));

    const std::string url = json_string(url_info, "url");
    if (url.empty()) {
        throw std::runtime_error(
            "no download url available (copyright restricted or quality not supported)");
    }
    std::string type = json_string(url_info, "type");
    if (type.empty()) {
        type = "mp3";
    }

    const std::string name = json_string(song, "name");
    const std::string filename =
        sanitize_filename(name + " [" + level + "]") + "." + type;
    const std::filesystem::path target =
        to_path(trim(directory)) / to_path(filename);

    std::error_code error_code;
    std::filesystem::create_directories(target.parent_path(), error_code);

    Http::Headers headers;
    headers.emplace_back("Referer", kReferer);
    std::string error;
    if (!http_.download(url, target.u8string(), headers, progress, error)) {
        throw std::runtime_error(error);
    }

    json out;
    out["path"] = target.u8string();
    out["filename"] = filename;
    out["name"] = name;
    out["artists"] = artists_join(json_array(song, "ar"));
    const json album = json_object(song, "al");
    out["album"] = json_string(album, "name");
    out["pic"] = album_pic_url(album);
    out["duration"] = json_int(song, "dt", 0);
    out["type"] = type;
    out["level"] = json_string(url_info, "level", level);
    out["size"] = json_int(url_info, "size", 0);
    out["size_formatted"] = format_size(json_int(url_info, "size", 0));
    return out.dump();
}

std::string Client::download_url(
    const std::string& url, const std::string& directory,
    const std::string& filename, const std::string& referer,
    const std::function<bool(std::uint64_t, std::uint64_t)>& progress,
    bool resume) {
    if (url.empty()) {
        throw std::runtime_error("empty download url");
    }
    std::string name = sanitize_filename(filename.empty() ? "download" : filename);
    if (name.find('.') == std::string::npos) {
        std::string path = url;
        const std::size_t query = path.find_first_of("?#");
        if (query != std::string::npos) {
            path = path.substr(0, query);
        }
        const std::size_t slash = path.find_last_of('/');
        const std::size_t dot = path.find_last_of('.');
        if (dot != std::string::npos &&
            (slash == std::string::npos || dot > slash) &&
            path.size() - dot <= 6) {
            name += path.substr(dot);
        }
    }
    const std::filesystem::path target =
        to_path(trim(directory)) / to_path(name);
    std::error_code error_code;
    std::filesystem::create_directories(target.parent_path(), error_code);

    Http::Headers headers;
    if (!referer.empty()) {
        headers.emplace_back("Referer", referer);
    }
    std::string error;
    if (!http_.download(url, target.u8string(), headers, progress, error, resume)) {
        throw std::runtime_error(error);
    }

    json out;
    out["path"] = target.u8string();
    out["filename"] = name;
    out["size"] = static_cast<std::int64_t>(
        std::filesystem::file_size(target, error_code));
    out["size_formatted"] = format_size(out["size"].get<std::int64_t>());
    return out.dump();
}

std::string Client::resolve_id(const std::string& input) {
    std::string text = trim(input);
    if (text.empty()) {
        throw std::runtime_error("empty song id or url");
    }

    if (text.find("163cn.tv") != std::string::npos) {
        const std::string final_url = http_.resolve_redirect(text);
        if (!final_url.empty()) {
            text = final_url;
        }
    }

    const bool is_url = text.find("music.163.com") != std::string::npos;
    const std::size_t id_pos = text.find("id=");
    if (is_url && id_pos != std::string::npos) {
        text = text.substr(id_pos + 3);
        const std::size_t amp = text.find('&');
        if (amp != std::string::npos) {
            text = text.substr(0, amp);
        }
        return trim(text);
    }

    bool all_digits = !text.empty();
    for (char c : text) {
        if (c < '0' || c > '9') {
            all_digits = false;
            break;
        }
    }
    if (all_digits) {
        return text;
    }

    std::size_t best_begin = std::string::npos;
    std::size_t best_len = 0;
    std::size_t begin = std::string::npos;
    for (std::size_t i = 0; i <= text.size(); ++i) {
        const bool digit = i < text.size() && text[i] >= '0' && text[i] <= '9';
        if (digit && begin == std::string::npos) {
            begin = i;
        } else if (!digit && begin != std::string::npos) {
            if (i - begin > best_len) {
                best_len = i - begin;
                best_begin = begin;
            }
            begin = std::string::npos;
        }
    }
    if (best_begin != std::string::npos && best_len >= 5) {
        return text.substr(best_begin, best_len);
    }
    return text;
}

std::string Client::pic_url_for(std::int64_t pic_id, int size) {
    return pic_url(pic_id, size);
}

}
